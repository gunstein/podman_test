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
            "backend.yaml": "todo-backend",
            "keycloak.yaml": "todo-keycloak",
            "frontend.yaml": "todo-frontend",
        }

        for filename, pod_name in expected.items():
            documents = list(yaml.safe_load_all(read(RUNTIME / filename)))
            pods = [doc for doc in documents if doc["kind"] == "Pod"]
            self.assertEqual(len(pods), 1)
            self.assertEqual(pods[0]["metadata"]["name"], pod_name)
            self.assertEqual(pods[0]["spec"]["restartPolicy"], "Never")

    def test_runtime_keeps_secrets_external(self):
        manifests = "\n".join(
            read(path) for path in RUNTIME.glob("*.yaml")
        )

        self.assertNotIn("kind: Secret", manifests)
        self.assertNotIn("stringData:", manifests)
        self.assertIn("secretName: todo-kube-backend-secret", manifests)
        self.assertIn("name: todo-kube-keycloak-secret", manifests)

    def test_frontend_reuses_the_accepted_tls_volume(self):
        frontend = read(RUNTIME / "frontend.yaml")

        self.assertIn("name: todo-nginx-data", frontend)
        self.assertNotIn("todo-kube-nginx-data", frontend)
        self.assertIn('volume.podman.io/uid: "101"', frontend)
        self.assertIn('volume.podman.io/gid: "101"', frontend)

    def test_systemd_keeps_existing_service_and_dependency_names(self):
        backend = read(RUNTIME / "todo-backend.kube")
        keycloak = read(RUNTIME / "todo-keycloak.kube")
        frontend = read(RUNTIME / "todo-frontend.kube")

        self.assertIn("Requires=todo-postgres.service", backend)
        self.assertIn("Requires=todo-postgres.service", keycloak)
        self.assertIn(
            "Requires=todo-backend.service todo-keycloak.service", frontend
        )
        self.assertNotIn("[Install]", backend + keycloak)
        self.assertIn("WantedBy=default.target", frontend)
        for unit in (backend, keycloak, frontend):
            self.assertIn("ExitCodePropagation=any", unit)
            self.assertIn("Restart=on-failure", unit)

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
            MIGRATION / "templates" / "todo-frontend.kube.j2"
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

        self.assertIn("todo-backend-backend", guide)
        self.assertIn("todo-backend.service", guide)
        self.assertIn("same YAML used in production", guide)

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
