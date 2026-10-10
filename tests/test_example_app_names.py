"""Platform code names no example app: a support check for docs/PLATFORM-PLAN.md.

The platform (installer, DR, templates, scripts, the proxy and Keycloak
images) must not depend on the example apps todo and notes. Today it still
does; KNOWN lists the files that name them, and each phase of the plan
removes files from it until it is empty.

This is support only. A file that names no example app can still assume one,
so passing this test does not prove the platform is general.

Two ways to fail:
- a file outside KNOWN names todo or notes: new coupling, remove it;
- a file in KNOWN no longer does: good, remove it from KNOWN.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = (
    "deploy/installer/app_installer", "deploy/dr/app_ops", "deploy/dr/app_dr_host", "deploy/dr/scripts",
    "deploy/dr/systemd", "deploy/manifests", "deploy/quadlet", "deploy/offline", "deploy/scripts",
    "proxy", "keycloak",
)
# A whole word, in any case: todo, Todo, TODO, todo-app, notes_app; not "denotes".
EXAMPLE_APP = re.compile(r"(?<![a-z])(todo|notes)(?![a-z])", re.IGNORECASE)

KNOWN = {
    "deploy/dr/app_dr_host/promoted.py",
    "deploy/dr/app_dr_host/replication_tls.py",
    "deploy/dr/app_ops/failover.py",
    "deploy/dr/app_ops/trust.py",
    "deploy/dr/scripts/app_backup.py",
    "deploy/dr/systemd/platform-backup.service",
    "deploy/dr/systemd/platform-backup.timer",
    "deploy/dr/systemd/platform-dr-check.service",
    "deploy/dr/systemd/platform-dr-check.timer",
    "deploy/dr/systemd/platform-replication-tls.service",
    "deploy/dr/systemd/platform-replication-tls.timer",
    "deploy/installer/app_installer/apps.py",
    "deploy/installer/app_installer/backup.py",
    "deploy/installer/app_installer/commands.py",
    "deploy/installer/app_installer/images.py",
    "deploy/installer/app_installer/install.py",
    "deploy/installer/app_installer/keycloak.py",
    "deploy/installer/app_installer/manifests.py",
    "deploy/installer/app_installer/stack.py",
    "deploy/installer/app_installer/target_render.py",
    "deploy/installer/app_installer/tls.py",
    "deploy/installer/app_installer/tls_secrets.py",
    "deploy/installer/app_installer/uninstall.py",
    "deploy/installer/app_installer/workloads.py",
    "deploy/manifests/app-config.yaml.j2",
    "deploy/manifests/keycloak.yaml.j2",
    "deploy/offline/install.sh",
    "deploy/offline/preflight.sh",
    "deploy/quadlet/keycloak.kube.j2",
    "deploy/quadlet/notes-app.kube.j2",
    "deploy/quadlet/notes-postgres.kube.j2",
    "deploy/quadlet/todo-app.kube.j2",
    "deploy/quadlet/todo-postgres.kube.j2",
    "deploy/scripts/app_ca.py",
    "deploy/scripts/dev/check_failover_login_page.py",
    "deploy/scripts/dev/run-e2e.sh",
    "deploy/scripts/dev/smoke-proxy.sh",
    "deploy/scripts/dev/spike_keycloak_pfx.py",
    "deploy/scripts/lab/acceptance.py",
    "deploy/scripts/lab/acceptance_preflight.py",
    "deploy/scripts/lab/helpers.sh",
    "deploy/scripts/lab/prepare-agent-snapshots.sh",
    "deploy/scripts/lab/provision-user.sh",
    "deploy/scripts/lab/pve_lab.py",
    "deploy/scripts/lab/trust-serving-ca.sh",
    "deploy/scripts/wait-ready.sh",
    "keycloak/Containerfile",
    "keycloak/todo-realm.json",
    "proxy/Containerfile",
    "proxy/proxy-entrypoint.sh",
}


def platform_files():
    """The platform's tracked files: code, templates, units and scripts, not tests or Markdown."""
    names = subprocess.check_output(["git", "ls-files", *PLATFORM], cwd=ROOT, text=True).split()
    return [name for name in names if "/tests/" not in name and not name.endswith(".md")]


def names_an_example_app(name):
    """Whether the file's path or its content names todo or notes."""
    return bool(EXAMPLE_APP.search(name) or EXAMPLE_APP.search((ROOT / name).read_text(errors="replace")))


class ExampleAppNameTests(unittest.TestCase):
    def test_no_new_platform_file_names_an_example_app(self):
        coupled = {name for name in platform_files() if names_an_example_app(name)}
        self.assertEqual(sorted(coupled - KNOWN), [], "new coupling to an example app")

    def test_known_files_still_name_an_example_app(self):
        coupled = {name for name in platform_files() if names_an_example_app(name)}
        self.assertEqual(sorted(KNOWN - coupled), [], "decoupled: remove these from KNOWN")


if __name__ == "__main__":
    unittest.main()
