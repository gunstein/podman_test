import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tests.runtime_fixture import RUNTIME, render_units

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class KubeRuntimeTests(unittest.TestCase):
    def test_runtime_yaml_parses_and_uses_canonical_pod_names(self):
        expected = {
            "todo-app.yaml": "todo-app",
            "keycloak.yaml": "keycloak",
            "todo-postgres.yaml": "todo-postgres",
            "shared-proxy.yaml": "shared-proxy",
            "notes-app.yaml": "notes-app",
            "notes-postgres.yaml": "notes-postgres",
        }

        for filename, pod_name in expected.items():
            documents = list(yaml.safe_load_all(read(RUNTIME / filename)))
            pods = [doc for doc in documents if doc["kind"] == "Pod"]
            self.assertEqual(len(pods), 1)
            self.assertEqual(pods[0]["metadata"]["name"], pod_name)
            self.assertEqual(pods[0]["spec"]["restartPolicy"], "Never")

        app = next(
            doc
            for doc in yaml.safe_load_all(read(RUNTIME / "todo-app.yaml"))
            if doc and doc.get("kind") == "Pod"
        )
        keycloak = next(
            doc
            for doc in yaml.safe_load_all(read(RUNTIME / "keycloak.yaml"))
            if doc and doc.get("kind") == "Pod"
        )
        self.assertEqual(
            [item["name"] for item in app["spec"]["initContainers"]],
            ["todo-migrate"],
        )
        self.assertEqual(
            [item["name"] for item in app["spec"]["containers"]],
            ["todo-backend", "todo-frontend"],
        )
        self.assertEqual(
            [item["name"] for item in keycloak["spec"]["containers"]],
            ["keycloak"],
        )

    def test_app_pod_groups_migration_backend_and_frontend(self):
        documents = list(yaml.safe_load_all(read(RUNTIME / "todo-app.yaml")))
        pod = next(doc for doc in documents if doc["kind"] == "Pod")

        self.assertEqual(
            [container["name"] for container in pod["spec"]["initContainers"]],
            ["todo-migrate"],
        )
        self.assertEqual(
            [container["name"] for container in pod["spec"]["containers"]],
            ["todo-backend", "todo-frontend"],
        )
        migrate = pod["spec"]["initContainers"][0]
        self.assertEqual(
            migrate["args"],
            [
                "python",
                "-m",
                "backend.migrate",
                "--connect-timeout",
                "120",
                "up",
            ],
        )
        self.assertIn("todo_migrator", str(migrate["env"]))
        self.assertIn("migrator-secret", str(migrate["volumeMounts"]))
        self.assertNotIn("migrator-secret", str(pod["spec"]["containers"]))

    def test_runtime_keeps_secrets_external(self):
        manifests = "\n".join(read(path) for path in RUNTIME.glob("*.yaml"))

        self.assertNotIn("kind: Secret", manifests)
        self.assertNotIn("stringData:", manifests)
        self.assertIn("secretName: \"todo-kube-backend-secret\"", manifests)
        self.assertIn("secretName: \"todo-kube-migrator-secret\"", manifests)
        self.assertIn("name: \"keycloak-kube-admin-secret\"", manifests)
        self.assertIn("secretName: \"keycloak-kube-postgres-secret\"", manifests)

    def test_proxy_reuses_the_accepted_tls_volume(self):
        app = read(RUNTIME / "todo-app.yaml")
        proxy = read(RUNTIME / "shared-proxy.yaml")
        self.assertIn("name: platform-nginx-data", proxy)
        self.assertNotIn("todo-kube-nginx-data", app)
        self.assertIn('volume.podman.io/uid: "101"', proxy)
        self.assertNotIn('volume.podman.io/gid: "101"', app)
        self.assertNotIn("platform-nginx-data", app)

    def test_systemd_represents_the_six_workload_boundaries(self):
        app = read(RUNTIME / "shared-proxy.kube")
        keycloak = read(RUNTIME / "keycloak.kube")
        postgres = read(RUNTIME / "todo-postgres.kube")

        self.assertIn("Requires=todo-app.service notes-app.service keycloak.service", app)
        self.assertIn("Requires=keycloak-postgres.service", keycloak)
        self.assertNotIn("[Install]", keycloak)
        self.assertIn("WantedBy=default.target", app)
        for unit in (app, keycloak, postgres, read(RUNTIME / "todo-app.kube"),
                     read(RUNTIME / "notes-app.kube"), read(RUNTIME / "notes-postgres.kube"),
                     read(RUNTIME / "keycloak-postgres.kube")):
            self.assertIn("PodmanArgs=--no-pod-prefix", unit)
            self.assertIn("ExitCodePropagation=any", unit)
            self.assertIn("Restart=on-failure", unit)
            if unit not in (keycloak, app):
                if "Yaml=notes-" in unit:
                    config = "notes-config.yaml"
                elif "Yaml=keycloak-" in unit:
                    config = "keycloak-config.yaml"
                else:
                    config = "todo-config.yaml"
                self.assertIn("ConfigMap=" + config, unit)
        self.assertNotIn("ConfigMap=", keycloak)
        self.assertNotIn("ConfigMap=", app)
        self.assertIn("name: keycloak-config", read(RUNTIME / "keycloak.yaml"))

        self.assertNotIn("ConfigMap=config-runtime.yaml", postgres)

    def test_proxy_routes_directly_to_app_and_identity_without_frontend_tls(self):
        docs = list(yaml.safe_load_all(read(RUNTIME / "shared-proxy.yaml")))
        pod = next(doc for doc in docs if doc["kind"] == "Pod")
        proxy = pod["spec"]["containers"]
        self.assertEqual([item["name"] for item in proxy], ["nginx"])
        self.assertEqual(proxy[0]["image"], "localhost/platform-proxy:m12")
        config = next(doc["data"]["nginx.conf"] for doc in docs
                      if doc["metadata"]["name"] == "shared-nginx-config")
        for upstream in ("todo-app:8080", "todo-app:8000", "keycloak:8080"):
            self.assertIn("server " + upstream + " resolve;", config)
        for route in ("todo_frontend", "todo_backend", "shared_keycloak"):
            self.assertIn("proxy_pass http://" + route + ";", config)
        for route in ("/health", "/ready", "/api/", "/auth/"):
            self.assertIn(route, config)
        self.assertNotIn("127.0.0.1:8000", config)
        self.assertNotIn("https://todo_frontend", config)
        self.assertNotIn("platform-nginx-data", read(RUNTIME / "todo-app.yaml"))
        self.assertNotIn("ssl_certificate", read(ROOT / "todo-frontend/nginx.conf"))
        self.assertIn("DATABASE_HOST: \"todo-postgres\"", read(RUNTIME / "todo-config.yaml"))

    def test_quadlet_conditionals_render_real_lan_and_loopback_profiles(self):
        proxy = read(RUNTIME / "shared-proxy.kube")
        self.assertIn("PublishPort=192.0.2.10:8443:8443", proxy)
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            render_units(output, "127.0.0.1", "192.0.2.11")
            local = read(output / "shared-proxy.kube")
            self.assertEqual(local.count("PublishPort=127.0.0.1:8443:8443"), 1)
            self.assertNotIn("192.0.2.10", local)
            self.assertIn("PublishPort=192.0.2.11:5432:5432",
                          read(output / "todo-postgres.kube"))
            for unit in output.glob("*.kube"):
                self.assertNotIn("{{", read(unit))
                self.assertNotIn("{%", read(unit))
        # Dependencies must point toward app/database, never back to ingress.
        for name in ("todo-app", "keycloak", "todo-postgres", "keycloak-postgres"):
            self.assertNotIn("shared-proxy.service", read(RUNTIME / (name + ".kube")))
        self.assertEqual(
            {path.name for path in (ROOT / "deploy/quadlet").glob("*.kube.j2")},
            {"app.kube.j2", "postgres.kube.j2", "keycloak.kube.j2", "shared-proxy.kube.j2"},
        )

    def test_superseded_separate_app_workloads_are_removed(self):
        for filename in (
            "backend.yaml",
            "frontend.yaml",
            "todo-backend.kube",
            "todo-frontend.kube",
        ):
            self.assertFalse((RUNTIME / filename).exists())

    def test_proxy_runtime_unit_maps_external_port_to_container_tls(self):
        template = read(ROOT / "deploy/quadlet/shared-proxy.kube.j2")
        self.assertIn("127.0.0.1:8080:8080", template)
        self.assertIn("service_port }}:8443", template)

    def test_jinja_manifests_are_the_single_workload_template_source(self):
        manifests_dir = ROOT / "deploy/manifests"
        self.assertEqual({path.name for path in manifests_dir.glob("*.yaml.j2")}, {
            "postgres.yaml.j2", "postgres-config.yaml.j2", "app-config.yaml.j2",
            "keycloak.yaml.j2", "shared-proxy.yaml.j2",
        })
        self.assertFalse((ROOT / "deploy/charts").exists())

        # One postgres.yaml.j2 backs every database; each app brings its own pod template.
        rendered = "\n".join(
            read(RUNTIME / filename) for filename in
            ("todo-postgres.yaml", "notes-postgres.yaml", "keycloak-postgres.yaml", "todo-app.yaml", "notes-app.yaml")
        )
        for pod_name in ("todo-postgres", "notes-postgres", "keycloak-postgres", "todo-app", "notes-app"):
            self.assertIn(f'name: "{pod_name}"', rendered)

        app_template = read(ROOT / "examples/todo/pod.yaml.j2")
        self.assertIn("{{ images.backend | tojson }}", app_template)
        proxy_template = read(manifests_dir / "shared-proxy.yaml.j2")
        self.assertIn("{{ image | tojson }}", proxy_template)

        # Every rendered PASSWORD env var is either a secret file path or a secretKeyRef;
        # no template ever carries a literal secret value (secrets.py owns names only).
        for filename in ("todo-postgres.yaml", "todo-app.yaml", "keycloak.yaml"):
            for doc in yaml.safe_load_all(read(RUNTIME / filename)):
                if doc["kind"] != "Pod":
                    continue
                for container in doc["spec"].get("initContainers", []) + doc["spec"]["containers"]:
                    for entry in container.get("env", []):
                        if "PASSWORD" not in entry["name"]:
                            continue
                        if "value" in entry:
                            self.assertTrue(entry["value"].endswith("/database-password"), entry)
                        else:
                            self.assertIn("secretKeyRef", entry["valueFrom"], entry)

    def test_clean_deploy_targets_kube_without_legacy_chain(self):
        from app_installer import platform_file
        self.assertEqual({workload.pod for workload in platform_file.checkout().workloads()}, {
            "todo-app", "notes-app", "keycloak", "todo-postgres", "notes-postgres",
            "keycloak-postgres", "shared-proxy"})
        self.assertIn("SourcePath", read(ROOT / "deploy/installer/app_installer/install.py"))

    def test_clean_dev_start_bootstraps_roles_before_shared_services(self):
        from app_installer import platform_file
        from app_installer.kube_play import up
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                patch("app_installer.kube_play.exists", return_value=False):
            up(RUNTIME, platform_file.checkout(), state_file=RUNTIME / '.dev-state.json')
        calls = [call.args[0] for call in run.call_args_list]
        postgres = next(i for i, a in enumerate(calls) if a[-1] == str(RUNTIME / "todo-postgres.yaml"))
        healthy = calls.index(["podman", "wait", "--condition", "healthy", "todo-postgres"])
        setup = [i for i, a in enumerate(calls) if a[-1] == "backend.setup_roles"]
        keycloak = next(i for i, a in enumerate(calls) if a[-1] == str(RUNTIME / "keycloak.yaml"))
        self.assertLess(postgres, healthy)
        self.assertLess(healthy, setup[0])
        self.assertLess(setup[0], keycloak)
        self.assertEqual(len(setup), 4)
        self.assertIn("app_installer install --mode dev", read(ROOT / "deploy/scripts/dev/dev-up.sh"))

    def test_offline_bundle_packages_only_the_rendered_target_files(self):
        offline = read(ROOT / "deploy/offline" / "build-bundle.sh")
        self.assertIn("python3 -m app_installer.bundle", offline)
        self.assertIn("deploy/installer/app_installer/", offline)
        # D7: no templates and no second render in the bundle.
        self.assertNotIn("render-kube-runtime.sh", offline)
        self.assertNotIn(".kube.j2", offline)
        self.assertNotIn("deploy/charts", offline)


if __name__ == "__main__":
    unittest.main()
