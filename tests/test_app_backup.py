import importlib.util
import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "deploy/scripts" / "app_backup.py"
SPEC = importlib.util.spec_from_file_location("app_backup", SCRIPT)
assert SPEC and SPEC.loader
app_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_backup)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    def __init__(
        self, recovery="f|off", containers=None, volumes=None,
        archive_status="on|000000010000000000000001||1h|1|0",
    ):
        self.recovery = recovery
        self.containers = set(containers or ())
        self.volumes = set(volumes or ())
        self.archive_status = archive_status
        self.commands = []

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        self.commands.append(command)

        if command[:3] == ["podman", "container", "exists"]:
            return completed(returncode=0 if command[3] in self.containers else 1)
        if command[:3] == ["podman", "volume", "exists"]:
            return completed(returncode=0 if command[3] in self.volumes else 1)
        if "psql" in command:
            sql = command[-1]
            if "pg_is_wal_replay_paused" in sql:
                return completed("t|t\n")
            if "pg_is_in_recovery" in sql:
                return completed(self.recovery + "\n")
            if "FROM pg_stat_archiver" in sql:
                return completed(self.archive_status + "\n")
            if "pg_create_restore_point" in sql:
                return completed("0/5000100\n")
            if "pg_walfile_name" in sql:
                return completed("000000010000000000000001\n")
            if "pg_switch_wal" in sql:
                return completed("0/6000000\n")
        return completed("ok\n")


class TodoBackupTests(unittest.TestCase):
    def tool(self, runner):
        return app_backup.TodoBackup(
            runner=runner,
            clock=lambda: datetime(2026, 8, 29, 12, 34, 56, tzinfo=timezone.utc),
            sleeper=lambda _seconds: None,
        )

    def test_status_reports_writable_archive(self):
        output = "\n".join(self.tool(FakeRunner()).status_lines())
        self.assertIn("Database writable: yes", output)
        self.assertIn("Archive mode: on", output)
        self.assertIn("Archive timeout: 1h", output)
        self.assertIn("Failed archive attempts: 0", output)

    def test_create_backup_runs_basebackup_and_verification(self):
        runner = FakeRunner()
        name = self.tool(runner).create_backup()
        self.assertEqual(name, "base-20260829T123456Z")
        flattened = [item for command in runner.commands for item in command]
        self.assertIn("pg_basebackup", flattened)
        self.assertIn("pg_verifybackup", flattened)
        self.assertIn("--manifest-checksums=SHA256", flattened)

    def test_create_backup_rejects_read_only_database(self):
        with self.assertRaisesRegex(app_backup.BackupError, "not a writable"):
            self.tool(FakeRunner(recovery="t|on")).create_backup()

    def test_restore_point_is_archived(self):
        runner = FakeRunner()
        lsn = self.tool(runner).create_restore_point("before_delete")
        self.assertEqual(lsn, "0/5000100")
        flattened = [item for command in runner.commands for item in command]
        self.assertIn("SELECT pg_create_restore_point('before_delete');", flattened)
        self.assertNotIn(":'restore_point'", flattened)
        self.assertIn("pg_switch_wal", " ".join(flattened))

    def test_restore_point_accepts_success_after_historical_archive_failure(self):
        runner = FakeRunner(
            archive_status=(
                "on|000000010000000000000001|000000010000000000000000|1h|2|1"
            )
        )
        self.tool(runner).create_restore_point("after_old_failure")

    def test_restore_rejects_existing_disposable_state_without_replace(self):
        restore_container = app_backup.apps.SHARED_RESOURCE_OWNER.resource("postgres-restore")
        runner = FakeRunner(containers={restore_container})
        with self.assertRaisesRegex(app_backup.BackupError, "--replace"):
            self.tool(runner).restore(
                "base-20260829T123456Z", "before_delete", replace=False
            )

    def test_restore_uses_only_fixed_disposable_targets(self):
        runner = FakeRunner()
        tool = self.tool(runner)
        tool.restore("base-20260829T123456Z", "before_delete", replace=False)
        flattened = [item for command in runner.commands for item in command]
        self.assertIn(tool.restore_container, flattened)
        self.assertIn(tool.restore_volume, flattened)
        self.assertNotIn("todo-postgres-data", flattened)

    def test_cleanup_requires_exact_confirmation(self):
        with self.assertRaisesRegex(app_backup.BackupError, "exactly"):
            self.tool(FakeRunner()).cleanup_restore("yes")

    def test_rejects_unsafe_backup_and_restore_point_names(self):
        with self.assertRaises(app_backup.BackupError):
            app_backup.TodoBackup._validate_backup_name("../../data")
        with self.assertRaises(app_backup.BackupError):
            app_backup.TodoBackup._validate_restore_point("bad point; rm")

    def test_restore_point_checks_exact_wal_file_after_archiver_advances(self):
        runner = FakeRunner(
            archive_status="on|000000010000000000000002||1h|2|0"
        )
        self.tool(runner).create_restore_point("archiver_advanced")
        expected = "/var/lib/postgresql/backup/wal/000000010000000000000001"
        self.assertTrue(any(expected in command for command in runner.commands))

    def test_short_control_command_timeout_is_actionable(self):
        def timeout_runner(arguments, timeout=None):
            raise subprocess.TimeoutExpired(arguments, timeout)

        with self.assertRaisesRegex(app_backup.BackupError, "timed out"):
            self.tool(timeout_runner).database_state()


class RestoreEdgeTests(unittest.TestCase):
    """Replacing old restore state, failed starts and timeouts never touch live data."""

    BACKUP = "base-20260829T123456Z"

    def tool(self, runner):
        return app_backup.TodoBackup(runner=runner, sleeper=lambda _seconds: None)

    def live_names(self, tool):
        app = tool.app
        return {app.resource("postgres"), app.volume("data"), app.volume("backup")}

    def removals(self, runner):
        return [command for command in runner.commands
                if command[:2] == ["podman", "rm"] or command[:3] == ["podman", "volume", "rm"]]

    def test_replace_removes_only_the_old_restore_state_then_restores(self):
        tool = self.tool(None)
        runner = FakeRunner(containers={tool.restore_container}, volumes={tool.restore_volume})
        tool.runner = runner
        tool.restore(self.BACKUP, "before_delete", replace=True)
        self.assertEqual(self.removals(runner), [
            ["podman", "rm", "--force", tool.restore_container],
            ["podman", "volume", "rm", tool.restore_volume]])
        create = runner.commands.index(["podman", "volume", "create", tool.restore_volume])
        self.assertGreater(create, runner.commands.index(["podman", "volume", "rm", tool.restore_volume]))
        removed = {name for command in self.removals(runner) for name in command}
        self.assertFalse(removed & self.live_names(tool))

    def test_replace_without_old_state_removes_nothing(self):
        runner = FakeRunner()
        self.tool(runner).restore(self.BACKUP, "before_delete", replace=True)
        self.assertEqual(self.removals(runner), [])

    def test_a_missing_base_backup_refuses_before_old_state_is_removed(self):
        class MissingBackup(FakeRunner):
            def __call__(self, arguments, timeout=None):
                if "PG_VERSION" in " ".join(arguments):
                    self.commands.append(list(arguments))
                    return completed(returncode=1, stderr="no such backup")
                return super().__call__(arguments, timeout)

        tool = self.tool(None)
        runner = MissingBackup(containers={tool.restore_container}, volumes={tool.restore_volume})
        tool.runner = runner
        with self.assertRaisesRegex(app_backup.BackupError, "Selected base backup check failed"):
            tool.restore(self.BACKUP, "before_delete", replace=True)
        self.assertEqual(self.removals(runner), [])

    def test_a_failed_start_removes_only_the_new_restore_container(self):
        class FailingStart(FakeRunner):
            def __call__(self, arguments, timeout=None):
                if arguments[:3] == ["podman", "run", "--detach"]:
                    self.commands.append(list(arguments))
                    return completed(returncode=125, stderr="cannot start")
                return super().__call__(arguments, timeout)

        runner = FailingStart()
        tool = self.tool(runner)
        with self.assertRaisesRegex(app_backup.BackupError, "Disposable PITR container start failed"):
            tool.restore(self.BACKUP, "before_delete", replace=False)
        self.assertEqual(self.removals(runner), [["podman", "rm", "--force", tool.restore_container]])

    def test_a_restore_that_never_pauses_or_stops_early_is_reported(self):
        class NeverPaused(FakeRunner):
            def __init__(self, stops):
                super().__init__()
                self.stops = stops

            def __call__(self, arguments, timeout=None):
                if "pg_is_wal_replay_paused" in arguments[-1]:
                    self.commands.append(list(arguments))
                    return completed("t|f\n")
                if arguments[:3] == ["podman", "container", "exists"]:
                    self.commands.append(list(arguments))
                    return completed(returncode=1 if self.stops else 0)
                return super().__call__(arguments, timeout)

        for stops, message in ((False, "did not reach the named restore point within 60 seconds"),
                               (True, "stopped during recovery")):
            with self.subTest(stops=stops):
                runner = NeverPaused(stops)
                with self.assertRaisesRegex(app_backup.BackupError, message):
                    self.tool(runner)._wait_for_restore_pause()

    def test_restore_status_without_a_restore_container(self):
        with self.assertRaisesRegex(app_backup.BackupError, "does not exist"):
            self.tool(FakeRunner()).restore_status()

    def test_wal_that_is_never_archived_or_cannot_be_inspected(self):
        for code, message in ((1, "was not archived within 30 seconds"), (125, "WAL archive inspection failed")):
            with self.subTest(code=code):
                def runner(arguments, timeout=None, code=code):
                    return completed(returncode=code, stderr="boom")
                with self.assertRaisesRegex(app_backup.BackupError, message):
                    self.tool(runner)._wait_for_archived_wal("000000010000000000000001")
        with self.assertRaisesRegex(app_backup.BackupError, "Unexpected WAL segment name"):
            self.tool(FakeRunner())._wait_for_archived_wal("../etc/passwd")

    def test_timeouts_during_restore_waits_are_operator_errors(self):
        def timeout_runner(arguments, timeout=None):
            raise subprocess.TimeoutExpired(arguments, timeout)

        tool = self.tool(timeout_runner)
        for call, message in ((tool._wait_for_restore_pause, "PITR status query timed out"),
                              (lambda: tool._wait_for_archived_wal("000000010000000000000001"),
                               "WAL archive inspection timed out"),
                              (lambda: tool._exists("volume", "x"), "inspection timed out")):
            with self.subTest(message=message), self.assertRaisesRegex(app_backup.BackupError, message):
                call()


class ApplicationBackupTests(unittest.TestCase):
    def test_each_backup_and_restore_stays_within_its_app(self):
        for app in app_backup.apps.REPLICATED_DATABASES:
            runner = FakeRunner()
            tool = app_backup.TodoBackup(runner=runner, app=app)
            tool.create_backup()
            tool.restore('base-20260829T123456Z', 'before_delete', False)
            commands = runner.commands
            backup = next(command for command in commands if 'pg_basebackup' in command)
            self.assertIn('--host=' + app.resource('postgres'), backup)
            self.assertIn('--username=' + app.database_role('replicator'), backup)
            self.assertIn(app.secret('replicator') + ',type=env,target=PGPASSWORD', backup)
            self.assertIn(app.volume('backup') + ':/backup:z', backup)
            for other in app_backup.apps.REPLICATED_DATABASES:
                if other == app:
                    continue
                self.assertFalse(any(other.resource('postgres') in argument
                                     for command in commands for argument in command))
            self.assertFalse(any(app.volume('data') in argument
                                 for command in commands for argument in command))
            self.assertTrue(any('--network' in command and 'none' in command for command in commands))
            self.assertTrue(any('recovery_target_action=pause' in command for command in commands))

    def test_cleanup_cannot_target_the_other_apps_restore(self):
        for app in app_backup.apps.REPLICATED_DATABASES:
            runner = FakeRunner(containers={app.resource('postgres-restore')},
                                volumes={app.volume('restore-data')})
            tool = app_backup.TodoBackup(runner=runner, app=app)
            with self.assertRaises(app_backup.BackupError):
                tool.cleanup_restore('yes')
            self.assertEqual(runner.commands, [])
            tool.cleanup_restore(app.resource('postgres-restore'))
            removals = [command for command in runner.commands if 'rm' in command]
            self.assertEqual(removals, [
                ['podman', 'rm', '--force', app.resource('postgres-restore')],
                ['podman', 'volume', 'rm', app.volume('restore-data')]])

    def test_group_backup_checks_last_app_before_first_base_backup(self):
        instances = [mock.Mock(), mock.Mock(), mock.Mock()]
        instances[0].archive_status.return_value = 'on|'
        instances[1].archive_status.return_value = 'on|'
        instances[2].require_writable_primary.side_effect = app_backup.BackupError('keycloak is read-only')
        with mock.patch.object(app_backup, 'TodoBackup', side_effect=instances):
            self.assertEqual(app_backup.main(['create']), 1)
        for tool in instances:
            tool.create_backup.assert_not_called()


class FakeHost:
    """One promoted host running every registered database, for the configure command."""

    def __init__(self, configured=(), directories_ready=(), mounts=None, source=None, active=True):
        self.databases = {d.resource('postgres'): d for d in app_backup.apps.REPLICATED_DATABASES}
        self.configured = set(configured)
        self.directories_ready = set(directories_ready)
        self.mounts = mounts or {}
        self.source = source or {}
        self.active = active
        self.commands = []

    def container_for(self, command):
        return next((c for c in command if c in self.databases), None)

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        self.commands.append(command)
        container = self.container_for(command)
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return completed("active\n" if self.active else "inactive\n", returncode=0 if self.active else 3)
        if command[:3] == ["systemctl", "--user", "show"]:
            database = next(d for d in self.databases.values() if d.service('postgres') == command[3])
            default = "/home/u/.config/containers/systemd/todo-kube-runtime/" + database.unit('postgres')
            return completed(self.source.get(database.name, default) + "\n")
        if command[:2] == ["systemctl", "--user"]:
            return completed()
        if command[:3] in (["podman", "secret", "exists"], ["podman", "volume", "exists"]):
            return completed()
        if command[:2] == ["podman", "inspect"]:
            database = self.databases[container]
            good = [{"Type": "volume", "Name": database.volume('backup'),
                     "Destination": "/var/lib/postgresql/backup", "RW": True}]
            return completed(json.dumps(self.mounts.get(database.name, good)))
        if command[:2] == ["podman", "run"] and app_backup.BACKUP_DIRECTORIES_SCRIPT in command:
            volume = command[command.index("--volume") + 1].split(":")[0]
            ready = volume in self.directories_ready
            self.directories_ready.add(volume)
            return completed("unchanged\n" if ready else "changed\n")
        if "psql" in command:
            sql = " ".join(command[command.index("psql"):])
            if "ALTER SYSTEM" in sql:
                self.configured.add(container)
                return completed("ALTER SYSTEM\n")
            if "current_setting('archive_command')" in sql:
                if container in self.configured:
                    return completed(f"on|{app_backup.ARCHIVE_COMMAND}|{app_backup.ARCHIVE_TIMEOUT}\n")
                return completed("off|(disabled)|0\n")
            if "pg_is_in_recovery" in sql:
                return completed("f|off\n")
            if "pg_create_restore_point" in sql:
                return completed("0/5000100\n")
            if "pg_walfile_name" in sql:
                return completed("000000010000000000000001\n")
        return completed()

    def index(self, predicate):
        return next(i for i, command in enumerate(self.commands) if predicate(command))

    def matching(self, predicate):
        return [command for command in self.commands if predicate(command)]


class ConfigureArchiveTests(unittest.TestCase):
    DATABASES = app_backup.apps.REPLICATED_DATABASES

    def configure(self, host, promoted=True, access_changed=False):
        tools = [app_backup.TodoBackup(runner=host, app=app, sleeper=lambda _s: None,
                                        clock=lambda: datetime(2026, 9, 25, 12, 0, 0, 123456, tzinfo=timezone.utc))
                 for app in self.DATABASES]
        self.hba = []
        self.waits = []
        journal = mock.Mock(side_effect=None if promoted else RuntimeError('A readable completed group '
                                                                                'promotion record is required'))
        with mock.patch.object(app_backup.replication, 'require_promoted_group', journal), \
                mock.patch.object(app_backup.replication, 'refresh_hba',
                                  side_effect=lambda app: (self.hba.append((len(host.commands), app.name)), access_changed)[1]), \
                mock.patch.object(app_backup.keycloak, 'wait',
                                  side_effect=lambda path, *a, **k: self.waits.append((path, k.get('hostname')))):
            return app_backup.configure(tools, Path('/journal.json'))

    def test_archive_settings_stay_byte_identical_to_deployed_hosts(self):
        self.assertEqual(app_backup.ARCHIVE_COMMAND,
                         'test ! -f /var/lib/postgresql/backup/wal/%f && cp %p /var/lib/postgresql/backup/wal/%f || '
                         'test "$(sha256sum < %p)" = "$(sha256sum < /var/lib/postgresql/backup/wal/%f)"')
        self.assertEqual(app_backup.ARCHIVE_TIMEOUT, '1h')

    def test_fresh_group_is_gated_first_then_restarted_behind_one_application_tier_stop(self):
        host = FakeHost()
        result = self.configure(host)
        names = [d.name for d in self.DATABASES]
        self.assertEqual(result['changed'], True)
        self.assertEqual(result['restarted'], names)
        self.assertEqual(sorted(result['verified']), sorted(names))
        self.assertEqual(result['verified']['todo'], 'todo_archive_check_20260925120000123456')
        last_gate = max(i for i, c in enumerate(host.commands) if c[:3] == ["systemctl", "--user", "show"])
        first_write = min([self.hba[0][0], host.index(lambda c: app_backup.BACKUP_DIRECTORIES_SCRIPT in c)])
        self.assertLess(last_gate, first_write)
        stops = host.matching(lambda c: c[:3] == ["systemctl", "--user", "stop"])
        self.assertEqual([c[3] for c in stops], app_backup.apps.services(databases=False))
        restarts = host.matching(lambda c: c[:3] == ["systemctl", "--user", "restart"])
        self.assertEqual([c[3] for c in restarts], [d.service('postgres') for d in self.DATABASES])
        start = host.index(lambda c: c == ["systemctl", "--user", "start", "shared-proxy.service"])
        self.assertLess(host.index(lambda c: c[:3] == ["systemctl", "--user", "stop"]),
                        host.index(lambda c: c[:3] == ["systemctl", "--user", "restart"]))
        self.assertLess(max(host.commands.index(c) for c in restarts), start)
        self.assertLess(start, host.index(lambda c: any('pg_create_restore_point' in part for part in c)))
        self.assertEqual(self.waits[:len(app_backup.apps.APPS)],
                         [('/ready', app.hostname) for app in app_backup.apps.APPS])

    def test_configured_group_is_left_running_and_unverified(self):
        host = FakeHost(configured={d.resource('postgres') for d in self.DATABASES},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        result = self.configure(host)
        self.assertEqual(result, {'changed': False, 'restarted': [], 'verified': {}})
        for verb in ("stop", "restart"):
            self.assertEqual(host.matching(lambda c: c[:3] == ["systemctl", "--user", verb]), [])
        self.assertFalse(host.matching(lambda c: any('ALTER SYSTEM' in part or 'pg_switch_wal' in part
                                                     for part in c)))
        self.assertTrue(host.matching(lambda c: c == ["systemctl", "--user", "start", "shared-proxy.service"]))

    def test_changed_replication_access_alone_reports_change_without_restart(self):
        host = FakeHost(configured={d.resource('postgres') for d in self.DATABASES},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        self.assertEqual(self.configure(host, access_changed=True),
                         {'changed': True, 'restarted': [], 'verified': {}})

    def test_invalid_mount_json_or_a_hung_stop_is_an_operator_error(self):
        class BrokenInspect(FakeHost):
            def __call__(self, arguments, timeout=None):
                if list(arguments[:2]) == ["podman", "inspect"]:
                    return completed("not json")
                return super().__call__(arguments, timeout)

        class HungStop(FakeHost):
            def __call__(self, arguments, timeout=None):
                if list(arguments[:3]) == ["systemctl", "--user", "stop"]:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                return super().__call__(arguments, timeout)

        with self.assertRaisesRegex(app_backup.BackupError, 'invalid JSON'):
            self.configure(BrokenInspect())
        with self.assertRaises(app_backup.BackupError):
            self.configure(HungStop())

    def test_only_the_changed_database_restarts_and_is_verified(self):
        last = self.DATABASES[-1]
        host = FakeHost(configured={d.resource('postgres') for d in self.DATABASES[:-1]},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        result = self.configure(host)
        self.assertEqual(result['restarted'], [last.name])
        self.assertEqual(list(result['verified']), [last.name])
        self.assertEqual(len(host.matching(lambda c: c[:3] == ["systemctl", "--user", "stop"])),
                         len(app_backup.apps.services(databases=False)))

    def test_a_failed_gate_on_the_last_database_stops_before_any_write(self):
        host = FakeHost(mounts={self.DATABASES[-1].name: []})
        with self.assertRaisesRegex(app_backup.BackupError, 'writable backup PVC'):
            self.configure(host)
        self.assertEqual(self.hba, [])
        self.assertFalse(host.matching(lambda c: app_backup.BACKUP_DIRECTORIES_SCRIPT in c
                                       or any('ALTER SYSTEM' in part for part in c)
                                       or c[:3] == ["systemctl", "--user", "stop"]))

    def test_backup_mount_must_be_exactly_the_writable_pvc_at_the_archive_path(self):
        for database in self.DATABASES:
            other = next(d for d in self.DATABASES if d != database)
            good = {"Type": "volume", "Name": database.volume('backup'),
                    "Destination": "/var/lib/postgresql/backup", "RW": True}
            for mounts, accepted in [([good], True), ([], False), ([good, good], False),
                                     ([{**good, "Name": other.volume('backup')}], False),
                                     ([{**good, "Destination": "/wrong"}], False),
                                     ([{**good, "RW": False}], False), ([{**good, "Type": "bind"}], False)]:
                with self.subTest(database=database.name, mounts=mounts):
                    tool = app_backup.TodoBackup(runner=FakeHost(mounts={database.name: mounts}), app=database)
                    if accepted:
                        tool.require_archive_prerequisites()
                    else:
                        with self.assertRaises(app_backup.BackupError):
                            tool.require_archive_prerequisites()

    def test_non_kube_or_inactive_postgres_is_refused(self):
        name = self.DATABASES[0].name
        with self.assertRaisesRegex(app_backup.BackupError, 'non-Kube'):
            self.configure(FakeHost(source={name: '/etc/containers/systemd/todo-postgres.container'}))
        with self.assertRaisesRegex(app_backup.BackupError, 'is not active'):
            self.configure(FakeHost(active=False))

    def test_incomplete_promotion_refuses_before_any_host_command(self):
        host = FakeHost()
        with self.assertRaisesRegex(app_backup.BackupError, 'completed group promotion'):
            self.configure(host, promoted=False)
        self.assertEqual(host.commands, [])

    def test_configure_always_covers_the_complete_group(self):
        self.assertEqual(app_backup.main(['--app', 'todo', 'configure']), 1)


if __name__ == "__main__":
    unittest.main()
