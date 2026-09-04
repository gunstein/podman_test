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
            self.assertIn("PodmanArgs=--no-pod-prefix", unit)
            self.assertIn("ExitCodePropagation=any", unit)
            self.assertIn("Restart=on-failure", unit)
            self.assertIn("ConfigMap=config.yaml", unit)

        self.assertNotIn("ConfigMap=config-runtime.yaml", postgres)

    def test_app_proxy_uses_loopback_and_shared_services_use_network_dns(self):
        development = read(RUNTIME / "config.yaml")
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
            yaml.safe_load_all(read(RUNTIME / "config.yaml"))
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

        self.assertIn("todo-backend", guide)
        self.assertIn("todo-frontend", guide)
        self.assertIn("--no-pod-prefix", guide)
        self.assertIn("todo-app.service", guide)
        self.assertIn("same chart", guide)

    def test_runtime_guide_separates_core_from_resilience(self):
        guide = read(RUNTIME / "README.md")

        self.assertIn("## Core architecture", guide)
        self.assertIn("## Operational resilience", guide)
        for filename in (
            "app.yaml",
            "keycloak.yaml",
            "postgres.yaml",
            "config.yaml",
            "todo-app.kube",
            "todo-keycloak.kube",
            "todo-postgres.kube",
            "todo.network",
        ):
            self.assertIn(filename, guide)

    def test_kube_index_routes_readers_to_the_current_runtime(self):
        index = read(ROOT / "kube" / "README.md")

        self.assertIn("Start with the [canonical runtime]", index)
        self.assertIn("three-workload architecture", index)
        self.assertIn("RESULTS-FOUR-POD-HISTORICAL.md", index)
        self.assertNotIn("Start with [poc/README.md]", index)

    def test_current_results_do_not_embed_four_pod_history(self):
        current = read(RUNTIME / "RESULTS.md")
        historical = read(
            RUNTIME / "RESULTS-FOUR-POD-HISTORICAL.md"
        )

        self.assertIn("Grouped Podman Kube runtime results", current)
        self.assertNotIn("four-pod static integration gate", current)
        self.assertIn("four-pod static integration gate", historical)

    def test_release_packages_include_the_shared_runtime(self):
        operations = read(ROOT / "scripts" / "build-operations-package.sh")
        offline = read(ROOT / "offline" / "build-bundle.sh")

        self.assertIn(
            'cp -r "$project_root/kube/runtime" "$package_directory/kube/"',
            operations,
        )
        self.assertIn("application_kube_runtime", operations)
        self.assertIn("postgres_kube_runtime", operations)
        self.assertIn("todo_fapolicyd", operations)
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

    def test_helm_is_the_single_workload_template_source(self):
        chart = ROOT / "helm" / "todo"
        values = read(chart / "values.yaml")
        rendered = "\n".join(
            read(RUNTIME / filename)
            for filename in ("app.yaml", "keycloak.yaml", "postgres.yaml")
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
        self.assertIn("{{ .Values.frontend.memory | quote }}", app_template)
        self.assertNotIn("password", values.lower())
        self.assertIn("# Source: todo/templates/app.yaml", rendered)

    def test_clean_deploy_targets_kube_without_legacy_chain(self):
        deploy = read(ROOT / "ansible" / "deploy.yml")
        runtime = read(
            ROOT / "ansible" / "roles" / "todo_kube_runtime"
            / "tasks" / "main.yml"
        )

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
