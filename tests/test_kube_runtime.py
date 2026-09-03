import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "kube" / "runtime"
MIGRATION = ROOT / "ansible" / "roles" / "kube_application_migration"
ROLLBACK = ROOT / "ansible" / "roles" / "kube_application_rollback"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class KubeRuntimeTests(unittest.TestCase):
    def test_runtime_yaml_parses_and_uses_canonical_pod_names(self):
        expected = {
            "app.yaml": "todo-app",
            "keycloak.yaml": "todo-keycloak",
            "postgres.yaml": "todo-postgres",
        }

        for filename, pod_name in expected.items():
            documents = list(yaml.safe_load_all(read(RUNTIME / filename)))
            pods = [doc for doc in documents if doc["kind"] == "Pod"]
            self.assertEqual(len(pods), 1)
            self.assertEqual(pods[0]["metadata"]["name"], pod_name)
            self.assertEqual(pods[0]["spec"]["restartPolicy"], "Never")

    def test_app_pod_groups_migration_backend_and_frontend(self):
        documents = list(yaml.safe_load_all(read(RUNTIME / "app.yaml")))
        pod = next(doc for doc in documents if doc["kind"] == "Pod")

        self.assertEqual(
            [container["name"] for container in pod["spec"]["initContainers"]],
            ["migrate"],
        )
        self.assertEqual(
            [container["name"] for container in pod["spec"]["containers"]],
            ["backend", "frontend"],
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
        manifests = "\n".join(
            read(path) for path in RUNTIME.glob("*.yaml")
        )

        self.assertNotIn("kind: Secret", manifests)
        self.assertNotIn("stringData:", manifests)
        self.assertIn("secretName: todo-kube-backend-secret", manifests)
        self.assertIn("secretName: todo-kube-migrator-secret", manifests)
        self.assertIn("name: todo-kube-keycloak-secret", manifests)

    def test_frontend_reuses_the_accepted_tls_volume(self):
        app = read(RUNTIME / "app.yaml")

        self.assertIn("name: todo-nginx-data", app)
        self.assertNotIn("todo-kube-nginx-data", app)
        self.assertIn('volume.podman.io/uid: "101"', app)
        self.assertIn('volume.podman.io/gid: "101"', app)
        self.assertIn("claimName: todo-nginx-data", app)

    def test_systemd_represents_the_three_workload_boundaries(self):
        app = read(RUNTIME / "todo-app.kube")
        keycloak = read(RUNTIME / "todo-keycloak.kube")
        postgres = read(RUNTIME / "todo-postgres.kube")

        self.assertIn(
            "Requires=todo-postgres.service todo-keycloak.service", app
        )
        self.assertIn("Requires=todo-postgres.service", keycloak)
        self.assertNotIn("[Install]", keycloak)
        self.assertIn("WantedBy=default.target", app)
        for unit in (app, keycloak, postgres):
            self.assertIn("ExitCodePropagation=any", unit)
            self.assertIn("Restart=on-failure", unit)

    def test_app_proxy_uses_loopback_and_shared_services_use_network_dns(self):
        development = read(RUNTIME / "config-dev.yaml")
        template = read(MIGRATION / "templates" / "config-runtime.yaml.j2")

        for config in (development, template):
            self.assertIn("server 127.0.0.1:8000;", config)
            self.assertIn("server todo-keycloak:8080;", config)
            self.assertIn("DATABASE_HOST: todo-postgres", config)

    def test_migration_delivers_separate_least_privilege_secrets(self):
        tasks = read(MIGRATION / "tasks" / "main.yml")
        rollback = read(ROLLBACK / "tasks" / "main.yml")

        self.assertIn("todo-migrator-password", tasks)
        self.assertIn("todo-kube-migrator-secret", tasks)
        self.assertIn("todo_kube_migrator_secret_payload", tasks)
        self.assertIn("todo-kube-migrator-secret", rollback)

    def test_superseded_separate_app_workloads_are_removed(self):
        for filename in (
            "backend.yaml",
            "frontend.yaml",
            "todo-backend.kube",
            "todo-frontend.kube",
        ):
            self.assertFalse((RUNTIME / filename).exists())

    def test_development_and_runtime_config_have_the_same_objects(self):
        development = list(
            yaml.safe_load_all(read(RUNTIME / "config-dev.yaml"))
        )
        template = read(MIGRATION / "templates" / "config-runtime.yaml.j2")
        names = {doc["metadata"]["name"] for doc in development}

        self.assertEqual(
            names,
            {
                "todo-postgres-config",
                "todo-backend-config",
                "todo-keycloak-config",
                "todo-nginx-env",
                "todo-nginx-config",
            },
        )
        application_names = names - {"todo-postgres-config"}
        for name in application_names:
            self.assertIn(f"name: {name}", template)
        self.assertIn("{{ todo_kube_service_origin }}", template)
        self.assertIn("{{ todo_kube_service_hostname }}", template)

        rendered = template.replace(
            "{{ todo_kube_service_origin }}", "https://todo.test:8443"
        ).replace("{{ todo_kube_service_hostname }}", "todo.test")
        rendered_documents = list(yaml.safe_load_all(rendered))
        self.assertEqual(
            {doc["metadata"]["name"] for doc in rendered_documents},
            application_names,
        )

    def test_frontend_runtime_unit_maps_external_port_to_container_tls(self):
        template = read(
            MIGRATION / "templates" / "todo-app.kube.j2"
        )

        self.assertIn("127.0.0.1:8080:8080", template)
        self.assertIn("todo_kube_service_port }}:8443", template)

    def test_migration_preserves_rollback_before_stopping_services(self):
        tasks = read(MIGRATION / "tasks" / "main.yml")

        preserve = tasks.index(
            "- name: Preserve the installed application Quadlets for rollback"
        )
        stop = tasks.index("- name: Stop the per-container application tier")
        remove = tasks.index("- name: Remove the replaced per-container Quadlets")

        self.assertLess(preserve, stop)
        self.assertLess(stop, remove)
        self.assertIn("force: true", tasks[preserve:stop])
        self.assertIn("todo_confirm_application_kube_migration", tasks)
        self.assertIn('failed_when: todo_kube_database_role.stdout | trim != "f|off"', tasks)

    def test_migration_does_not_replace_postgres_or_persistent_data(self):
        tasks = read(MIGRATION / "tasks" / "main.yml")

        self.assertNotIn("systemctl, --user, stop, todo-postgres.service", tasks)
        self.assertNotIn("todo-postgres.container\n    state: absent", tasks)
        self.assertNotIn("podman, volume, rm", tasks)
        self.assertNotIn("todo-postgres-data", tasks)

    def test_rollback_requires_complete_backup_before_runtime_changes(self):
        tasks = read(ROLLBACK / "tasks" / "main.yml")

        require = tasks.index("- name: Require the complete rollback set")
        stop = tasks.index("- name: Stop the Kube application tier")
        restore = tasks.index("- name: Restore the preserved per-container Quadlets")

        self.assertLess(require, stop)
        self.assertLess(stop, restore)
        self.assertIn("todo_confirm_application_quadlet_rollback", tasks)
        self.assertNotIn("todo-postgres-data", tasks)

    def test_documentation_explains_podman_container_names(self):
        guide = read(RUNTIME / "README.md")

        self.assertIn("todo-app-backend", guide)
        self.assertIn("todo-app-frontend", guide)
        self.assertIn("todo-app.service", guide)
        self.assertIn("same workload YAML", guide)

    def test_runtime_guide_separates_core_from_resilience(self):
        guide = read(RUNTIME / "README.md")

        self.assertIn("## Core architecture", guide)
        self.assertIn("## Operational resilience", guide)
        for filename in (
            "app.yaml",
            "keycloak.yaml",
            "postgres.yaml",
            "config-dev.yaml",
            "todo-app.kube",
            "todo-keycloak.kube",
            "todo-postgres.kube",
            "todo.network",
        ):
            self.assertIn(filename, guide)

    def test_release_packages_include_the_shared_runtime(self):
        operations = read(ROOT / "scripts" / "build-operations-package.sh")
        offline = read(ROOT / "offline" / "build-bundle.sh")

        self.assertIn(
            'cp -r "$project_root/kube/runtime" "$package_directory/kube/"',
            operations,
        )
        self.assertIn("kube_application_migration", operations)
        self.assertIn("kube_application_rollback", operations)
        self.assertIn("migrate-application-to-kube.yml", operations)
        self.assertIn(
            "rollback-application-to-container-quadlets.yml", operations
        )
        self.assertIn("kube_postgres_primary_migration", operations)
        self.assertIn("kube_postgres_primary_rollback", operations)
        self.assertIn("migrate-postgres-primary-to-kube.yml", operations)
        self.assertIn(
            "rollback-postgres-primary-to-container-quadlet.yml", operations
        )
        self.assertIn(
            'cp -r "$project_root/kube/runtime" "$bundle_directory/kube/"',
            offline,
        )


if __name__ == "__main__":
    unittest.main()
