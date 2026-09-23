import json
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
            "app.yaml": "todo-app",
            "keycloak.yaml": "keycloak",
            "postgres.yaml": "todo-postgres",
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
            for doc in yaml.safe_load_all(read(RUNTIME / "app.yaml"))
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
        documents = list(yaml.safe_load_all(read(RUNTIME / "app.yaml")))
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
        self.assertIn("secretName: todo-kube-backend-secret", manifests)
        self.assertIn("secretName: todo-kube-migrator-secret", manifests)
        self.assertIn("name: keycloak-kube-admin-secret", manifests)
        self.assertIn("secretName: keycloak-kube-postgres-secret", manifests)

    def test_proxy_reuses_the_accepted_tls_volume(self):
        app = read(RUNTIME / "app.yaml")
        proxy = read(RUNTIME / "shared-proxy.yaml")
        self.assertIn("name: todo-nginx-data", proxy)
        self.assertNotIn("todo-kube-nginx-data", app)
        self.assertIn('volume.podman.io/uid: "101"', proxy)
        self.assertNotIn('volume.podman.io/gid: "101"', app)
        self.assertNotIn("todo-nginx-data", app)

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
            if unit != keycloak:
                if "Yaml=notes-" in unit:
                    config = "notes-config.yaml"
                elif "Yaml=keycloak-" in unit:
                    config = "keycloak-config.yaml"
                else:
                    config = "config.yaml"
                self.assertIn("ConfigMap=" + config, unit)
        self.assertNotIn("ConfigMap=", keycloak)
        self.assertIn("name: keycloak-config", read(RUNTIME / "keycloak.yaml"))

        self.assertNotIn("ConfigMap=config-runtime.yaml", postgres)

    def test_proxy_routes_directly_to_app_and_identity_without_frontend_tls(self):
        docs = list(yaml.safe_load_all(read(RUNTIME / "shared-proxy.yaml")))
        pod = next(doc for doc in docs if doc["kind"] == "Pod")
        proxy = pod["spec"]["containers"]
        self.assertEqual([item["name"] for item in proxy], ["nginx"])
        self.assertEqual(proxy[0]["image"], "localhost/todo-proxy:m12")
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
        self.assertNotIn("todo-nginx-data", read(RUNTIME / "app.yaml"))
        self.assertNotIn("ssl_certificate", read(ROOT / "todo-frontend/nginx.conf"))
        self.assertIn("DATABASE_HOST: todo-postgres", read(RUNTIME / "config.yaml"))

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
            {"todo-app.kube.j2", "keycloak.kube.j2",
             "todo-postgres.kube.j2", "shared-proxy.kube.j2",
             "notes-app.kube.j2", "notes-postgres.kube.j2", "keycloak-postgres.kube.j2"},
        )
        self.assertEqual(list((ROOT / "deploy/ansible/roles").rglob("*.kube.j2")), [])

    def test_active_application_constructs_separate_secrets_in_memory(self):
        from todo_installer import secrets
        with patch.object(secrets, "read", return_value="fixture-password"), \
                patch.object(secrets, "exists", return_value=False), \
                patch.object(secrets, "run") as run:
            secrets.create_kube({**secrets.application_secret_mapping(secrets.apps.APPS[0]),
                                 **secrets.keycloak_secret_mapping()})
        payloads = {call.args[3]: json.loads(call.kwargs["input"])
                    for call in run.call_args_list}
        self.assertEqual(set(payloads), {
            "todo-kube-migrator-secret", "todo-kube-backend-secret", "keycloak-kube-admin-secret"})
        for name, payload in payloads.items():
            self.assertEqual(payload["kind"], "Secret")
            key = "bootstrap-admin-password" if name == "keycloak-kube-admin-secret" else "database-password"
            self.assertEqual(payload["data"][key], "Zml4dHVyZS1wYXNzd29yZA==")

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
        self.assertIn("todo_service_port }}:8443", template)

    def test_helm_is_the_single_workload_template_source(self):
        chart = ROOT / "deploy/charts" / "todo"
        values = read(chart / "values.yaml")
        rendered = "\n".join(
            read(RUNTIME / filename) for filename in ("app.yaml", "keycloak.yaml", "postgres.yaml")
        )

        for filename in ("app.yaml", "postgres.yaml", "config.yaml"):
            self.assertTrue((chart / "templates" / filename).is_file())
        self.assertFalse((chart / "templates/keycloak.yaml").exists())
        self.assertTrue((ROOT / "deploy/charts/keycloak/templates/keycloak.yaml").is_file())
        self.assertIn("# Source: keycloak/templates/keycloak.yaml", rendered)
        app_template = read(chart / "templates" / "app.yaml")
        self.assertIn("{{ .Values.backend.image | quote }}", app_template)
        proxy_template = read(ROOT / "deploy/charts/shared-proxy/templates/shared-proxy.yaml")
        self.assertIn("{{ .Values.proxy.memory | quote }}", proxy_template)
        self.assertNotIn("password", values.lower())
        self.assertIn("# Source: todo/templates/app.yaml", rendered)

    def test_clean_deploy_targets_kube_without_legacy_chain(self):
        from todo_installer import install
        deploy = read(ROOT / "deploy/ansible/playbooks/deploy.yml")
        self.assertIn("todo_installer", deploy)
        self.assertNotIn("include_role", deploy)
        self.assertEqual(set(install.SERVICES), {
            "todo-app", "notes-app", "keycloak", "todo-postgres", "notes-postgres",
            "keycloak-postgres", "shared-proxy"})
        self.assertIn("SourcePath", read(ROOT / "deploy/installer/todo_installer/install.py"))

    def test_clean_dev_start_bootstraps_roles_before_shared_services(self):
        from todo_installer.kube_play import up
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                patch("todo_installer.kube_play.exists", return_value=False):
            up(RUNTIME, state_file=RUNTIME / '.dev-state.json')
        calls = [call.args[0] for call in run.call_args_list]
        postgres = next(i for i, a in enumerate(calls) if a[-1] == str(RUNTIME / "postgres.yaml"))
        healthy = calls.index(["podman", "wait", "--condition", "healthy", "todo-postgres"])
        setup = [i for i, a in enumerate(calls) if a[-1] == "backend.setup_roles"]
        keycloak = next(i for i, a in enumerate(calls) if a[-1] == str(RUNTIME / "keycloak.yaml"))
        self.assertLess(postgres, healthy)
        self.assertLess(healthy, setup[0])
        self.assertLess(setup[0], keycloak)
        self.assertEqual(len(setup), 4)
        self.assertIn("todo_installer install --mode dev", read(ROOT / "deploy/scripts/dev-up.sh"))

    def test_offline_bundle_packages_rendered_kube_runtime(self):
        offline = read(ROOT / "deploy/offline" / "build-bundle.sh")
        self.assertIn('deploy/scripts/render-kube-runtime.sh"', offline)
        self.assertIn("deploy/installer/todo_installer/", offline)
        self.assertIn("deploy/charts/.", offline)


if __name__ == "__main__":
    unittest.main()
