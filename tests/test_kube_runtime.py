import pathlib
import tempfile
import unittest

import yaml

from tests.runtime_fixture import RUNTIME, render_units

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class KubeRuntimeTests(unittest.TestCase):
    def test_runtime_yaml_parses_and_uses_canonical_pod_names(self):
        expected = {
            "app.yaml": "todo-app",
            "keycloak.yaml": "todo-keycloak",
            "postgres.yaml": "todo-postgres",
            "shared-proxy.yaml": "shared-proxy",
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
            ["todo-keycloak"],
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
        self.assertIn("name: todo-kube-keycloak-secret", manifests)

    def test_proxy_reuses_the_accepted_tls_volume(self):
        app = read(RUNTIME / "app.yaml")
        proxy = read(RUNTIME / "shared-proxy.yaml")
        self.assertIn("name: todo-nginx-data", proxy)
        self.assertNotIn("todo-kube-nginx-data", app)
        self.assertIn('volume.podman.io/uid: "101"', proxy)
        self.assertNotIn('volume.podman.io/gid: "101"', app)
        self.assertNotIn("todo-nginx-data", app)

    def test_systemd_represents_the_four_workload_boundaries(self):
        app = read(RUNTIME / "shared-proxy.kube")
        keycloak = read(RUNTIME / "todo-keycloak.kube")
        postgres = read(RUNTIME / "todo-postgres.kube")

        self.assertIn("Requires=todo-app.service todo-keycloak.service", app)
        self.assertIn("Requires=todo-postgres.service", keycloak)
        self.assertNotIn("[Install]", keycloak)
        self.assertIn("WantedBy=default.target", app)
        for unit in (app, keycloak, postgres, read(RUNTIME / "todo-app.kube")):
            self.assertIn("PodmanArgs=--no-pod-prefix", unit)
            self.assertIn("ExitCodePropagation=any", unit)
            self.assertIn("Restart=on-failure", unit)
            self.assertIn("ConfigMap=config.yaml", unit)

        self.assertNotIn("ConfigMap=config-runtime.yaml", postgres)

    def test_proxy_routes_directly_to_app_and_identity_without_frontend_tls(self):
        docs = list(yaml.safe_load_all(read(RUNTIME / "shared-proxy.yaml")))
        pod = next(doc for doc in docs if doc["kind"] == "Pod")
        proxy = pod["spec"]["containers"]
        self.assertEqual([item["name"] for item in proxy], ["nginx"])
        self.assertEqual(proxy[0]["image"], "localhost/todo-proxy:m12")
        config = next(doc["data"]["nginx.conf"] for doc in docs
                      if doc["metadata"]["name"] == "shared-nginx-config")
        for upstream in ("todo-app:8080", "todo-app:8000", "todo-keycloak:8080"):
            self.assertIn("server " + upstream + ";", config)
        for route in ("todo_frontend", "todo_backend", "todo_keycloak"):
            self.assertIn("proxy_pass http://" + route + ";", config)
        for route in ("/health", "/ready", "/api/", "/auth/"):
            self.assertIn(route, config)
        self.assertNotIn("127.0.0.1:8000", config)
        self.assertNotIn("https://todo_frontend", config)
        self.assertNotIn("todo-nginx-data", read(RUNTIME / "app.yaml"))
        self.assertNotIn("ssl_certificate", read(ROOT / "frontend/nginx.conf"))
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
        for name in ("todo-app", "todo-keycloak", "todo-postgres"):
            self.assertNotIn("shared-proxy.service", read(RUNTIME / (name + ".kube")))
        for name, role in (("todo-app", "application_kube_runtime"),
                           ("todo-keycloak", "application_kube_runtime"),
                           ("shared-proxy", "shared_proxy_runtime")):
            relative = "templates/" + name + ".kube.j2"
            self.assertEqual(read(ROOT / "ansible/roles/todo_kube_runtime" / relative),
                             read(ROOT / "ansible/roles" / role / relative))

    def test_active_application_constructs_separate_secrets_in_memory(self):
        tasks = read(ROOT / "ansible" / "roles" / "application_kube_runtime" / "tasks" / "main.yml")
        for name in (
            "todo-migrator-password",
            "todo-app-password",
            "todo-kube-migrator-secret",
            "todo-kube-backend-secret",
            "todo-kube-keycloak-secret",
        ):
            self.assertIn(name, tasks)
        self.assertIn("no_log: true", tasks)
        self.assertIn("stdin:", tasks)
        self.assertIn("b64encode", tasks)

    def test_superseded_separate_app_workloads_are_removed(self):
        for filename in (
            "backend.yaml",
            "frontend.yaml",
            "todo-backend.kube",
            "todo-frontend.kube",
        ):
            self.assertFalse((RUNTIME / filename).exists())

    def test_proxy_runtime_unit_maps_external_port_to_container_tls(self):
        template = read(
            ROOT
            / "ansible"
            / "roles"
            / "shared_proxy_runtime"
            / "templates"
            / "shared-proxy.kube.j2"
        )
        self.assertIn("127.0.0.1:8080:8080", template)
        self.assertIn("todo_service_port }}:8443", template)

    def test_helm_is_the_single_workload_template_source(self):
        chart = ROOT / "helm" / "todo"
        values = read(chart / "values.yaml")
        rendered = "\n".join(
            read(RUNTIME / filename) for filename in ("app.yaml", "keycloak.yaml", "postgres.yaml")
        )

        for filename in (
            "app.yaml",
            "keycloak.yaml",
            "postgres.yaml",
            "config.yaml",
        ):
            self.assertTrue((chart / "templates" / filename).is_file())
        app_template = read(chart / "templates" / "app.yaml")
        self.assertIn("{{ .Values.backend.image | quote }}", app_template)
        proxy_template = read(ROOT / "helm/shared-proxy/templates/shared-proxy.yaml")
        self.assertIn("{{ .Values.proxy.memory | quote }}", proxy_template)
        self.assertNotIn("password", values.lower())
        self.assertIn("# Source: todo/templates/app.yaml", rendered)

    def test_clean_deploy_targets_kube_without_legacy_chain(self):
        deploy = read(ROOT / "ansible" / "deploy.yml")
        runtime = read(ROOT / "ansible" / "roles" / "todo_kube_runtime" / "tasks" / "main.yml")

        self.assertIn("name: todo_kube_runtime", deploy)
        for legacy in (
            "todo-backend.container",
            "todo-frontend.container",
            "todo-migrate.container",
        ):
            self.assertNotIn(legacy, deploy)
        self.assertIn("Start PostgreSQL through its Kube unit", runtime)
        self.assertIn("Provision database roles", runtime)
        self.assertIn("Start the grouped application", runtime)

    def test_clean_dev_start_bootstraps_roles_before_shared_services(self):
        script = read(ROOT / "scripts" / "dev-up.sh")
        postgres = script.index('"$generated/postgres.yaml"')
        healthy = script.index("podman wait --condition healthy")
        first_setup = script.index("setup_roles\npodman kube play", healthy)
        keycloak = script.index('"$generated/keycloak.yaml"')

        self.assertLess(postgres, healthy)
        self.assertLess(healthy, first_setup)
        self.assertLess(first_setup, keycloak)
        self.assertEqual(script.splitlines().count("setup_roles"), 2)

    def test_offline_bundle_packages_rendered_kube_runtime(self):
        offline = read(ROOT / "offline" / "build-bundle.sh")
        self.assertIn('scripts/render-kube-runtime.sh"', offline)
        self.assertIn("ansible/roles/todo_kube_runtime", offline)
        self.assertIn("helm/todo", offline)


if __name__ == "__main__":
    unittest.main()
