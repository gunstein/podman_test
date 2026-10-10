"""V1: podman kube play copies each secret volume's files into a named volume
called after the Kube secret, and keeps it after kube down and after the
secret is removed. Those copies go with the pods (secrets.remove_kube_volumes)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, kube_play, platform_file, secrets  # noqa: E402
from fake_host import FakeHost  # noqa: E402


class KubeSecretVolumeTests(unittest.TestCase):
    def test_every_kube_secret_mounted_as_a_volume_is_named(self):
        # The manifests mount these Kube secrets as `secret:` volumes (Keycloak's own
        # are environment variables, which leave no volume, and removing nothing is fine).
        names = secrets.kube_volume_names(platform_file.checkout())
        for app in platform_file.checkout().database_apps:
            for name in (app.database.kube_secret, app.kube_secret('backend'), app.kube_secret('migrator')):
                self.assertIn(name, names)
        self.assertIn(apps.KEYCLOAK_DATABASE.kube_secret, names)
        self.assertIn(apps.PROXY_KUBE_TLS_SECRET, names)

    def test_only_volumes_no_container_uses_go(self):
        unused, used = platform_file.checkout().apps[0].kube_secret('backend'), platform_file.checkout().apps[1].kube_secret('backend')
        with FakeHost() as host:
            host.volumes = {unused, used, platform_file.checkout().apps[0].database.volume('data')}
            host.volumes_in_use = {used}
            self.assertTrue(secrets.remove_kube_volumes(platform_file.checkout()))
            self.assertEqual(host.volumes, {used, platform_file.checkout().apps[0].database.volume('data')})
            self.assertFalse(secrets.remove_kube_volumes(platform_file.checkout()))

    def test_dev_down_removes_them_after_the_pods(self):
        with tempfile.TemporaryDirectory() as temp, FakeHost() as host:
            state = Path(temp) / 'dev.json'
            state.write_text(json.dumps({'fingerprint': 'x', 'teardown': ['kind: Pod # todo-app']}))
            host.volumes = {platform_file.checkout().apps[0].kube_secret('migrator'), platform_file.checkout().apps[0].database.volume('data')}
            self.assertTrue(kube_play.down(Path(temp), platform_file.checkout(), state_file=state))
            self.assertEqual(host.volumes, {platform_file.checkout().apps[0].database.volume('data')})
            down = host.calls.index(['podman', 'kube', 'play', '--down', '-'])
            removal = host.calls.index(['podman', 'volume', 'rm', platform_file.checkout().apps[0].kube_secret('migrator')])
            self.assertLess(down, removal)


if __name__ == '__main__':
    unittest.main()
