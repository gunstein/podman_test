import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POC = ROOT / "kube" / "poc"
BACKEND = ROOT / "kube" / "backend"
NGINX = ROOT / "kube" / "nginx"
KEYCLOAK = ROOT / "kube" / "keycloak"
POSTGRES = ROOT / "kube" / "postgres"
REPLICATION = ROOT / "kube" / "replication"


def read(name: str) -> str:
    return (POC / name).read_text(encoding="utf-8")


class KubePocTests(unittest.TestCase):
    def test_poc_is_isolated_from_the_accepted_runtime(self):
        tracked_runtime = {
            path.name
            for path in (ROOT / "quadlet").iterdir()
            if path.is_file()
        }
        poc_files = {path.name for path in POC.iterdir() if path.is_file()}

        self.assertTrue(tracked_runtime.isdisjoint(poc_files))
        self.assertTrue(
            all(
                "todo-kube-poc" in path.name
                or path.name
                in {
                    "README.md",
                    "RESULTS.md",
                    "server.yaml",
                    "consumer.yaml",
                }
                for path in POC.iterdir()
                if path.is_file()
            )
        )

    def test_independent_pods_use_explicit_restart_policy(self):
        server = read("server.yaml")
        consumer = read("consumer.yaml")

        self.assertIn("name: todo-kube-poc-server", server)
        self.assertIn("name: todo-kube-poc-consumer", consumer)
        self.assertIn("restartPolicy: Never", server)
        self.assertIn("restartPolicy: Never", consumer)
        self.assertIn("imagePullPolicy: Never", server)
        self.assertIn("imagePullPolicy: Never", consumer)
        self.assertIn("memory: 128Mi", server)
        self.assertIn("memory: 128Mi", consumer)
        self.assertNotIn("cpu:", server + consumer)
        self.assertIn("livenessProbe:\n        exec:", server)
        self.assertIn("socket.create_connection", server)
        self.assertIn("containerPort: 8000", server)
        self.assertNotIn("http.server", server)
        self.assertNotIn("tcpSocket:", server)
        for manifest in (server, consumer):
            self.assertIn("runAsUser: 1000", manifest)
            self.assertIn("runAsGroup: 1000", manifest)

    def test_external_secret_is_not_embedded(self):
        consumer = read("consumer.yaml")
        all_yaml = "\n".join(
            path.read_text(encoding="utf-8")
            for path in POC.glob("*.yaml")
        )
        yaml_fields = {line.strip() for line in all_yaml.splitlines()}

        self.assertIn("secretName: todo-kube-poc-secret", consumer)
        self.assertNotIn("kind: Secret", all_yaml)
        self.assertNotIn("stringData:", yaml_fields)
        self.assertNotIn("data:", yaml_fields)

    def test_kube_quadlets_use_same_yaml_and_report_failures(self):
        server_unit = read("todo-kube-poc-server.kube")
        consumer_unit = read("todo-kube-poc-consumer.kube")

        self.assertIn("Yaml=server.yaml", server_unit)
        self.assertIn("Yaml=consumer.yaml", consumer_unit)
        self.assertIn("Network=todo-kube-poc.network", server_unit)
        self.assertIn("Network=todo-kube-poc.network", consumer_unit)
        self.assertIn("ExitCodePropagation=any", server_unit)
        self.assertIn("ExitCodePropagation=any", consumer_unit)
        self.assertNotIn(
            "KubeDownForce=true", server_unit + consumer_unit
        )

    def test_persistent_volume_is_not_forced_down(self):
        consumer = read("consumer.yaml")
        readme = read("README.md")

        self.assertIn("persistentVolumeClaim:", consumer)
        self.assertIn("claimName: todo-kube-poc-data", consumer)
        self.assertIn("podman volume rm todo-kube-poc-data", readme)
        self.assertNotIn("podman kube play --down --force", readme)


class BackendKubeCandidateTests(unittest.TestCase):
    def test_candidate_is_parallel_and_has_no_host_port(self):
        manifest = (BACKEND / "backend.yaml").read_text(encoding="utf-8")

        self.assertIn("name: todo-kube-backend", manifest)
        self.assertIn("containerPort: 8000", manifest)
        self.assertNotIn("hostPort:", manifest)
        self.assertIn("restartPolicy: Never", manifest)
        self.assertIn("imagePullPolicy: Never", manifest)

    def test_candidate_preserves_backend_runtime_contract(self):
        manifest = (BACKEND / "backend.yaml").read_text(encoding="utf-8")
        config = (BACKEND / "config-lab.yaml").read_text(encoding="utf-8")

        self.assertIn("DATABASE_PASSWORD_FILE", manifest)
        self.assertIn("/run/secrets/todo-backend/database-password", manifest)
        self.assertIn("secretName: todo-kube-backend-secret", manifest)
        self.assertIn("configMapRef:", manifest)
        self.assertIn("name: todo-backend-config", manifest)
        self.assertIn("DATABASE_HOST: todo-postgres", config)
        self.assertIn("DATABASE_USER: todo_app", config)
        self.assertIn("OIDC_JWKS_URL: http://todo-keycloak:8080", config)

    def test_candidate_security_and_health_are_explicit(self):
        manifest = (BACKEND / "backend.yaml").read_text(encoding="utf-8")

        self.assertIn("runAsUser: 1000", manifest)
        self.assertIn("runAsGroup: 1000", manifest)
        self.assertIn("allowPrivilegeEscalation: false", manifest)
        self.assertIn("capabilities:\n          drop:\n            - ALL", manifest)
        self.assertIn("memory: 256Mi", manifest)
        self.assertNotIn("cpu:", manifest)
        self.assertIn("livenessProbe:\n        exec:", manifest)

    def test_candidate_quadlet_uses_external_inputs_and_systemd_restart(self):
        unit = (BACKEND / "todo-kube-backend.kube").read_text(
            encoding="utf-8"
        )

        self.assertIn("Yaml=backend.yaml", unit)
        self.assertIn("ConfigMap=config-lab.yaml", unit)
        self.assertIn("Network=todo.network", unit)
        self.assertIn("ExitCodePropagation=any", unit)
        self.assertIn("LogDriver=journald", unit)
        self.assertIn("Restart=on-failure", unit)

    def test_candidate_does_not_store_secret_material(self):
        yaml_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in BACKEND.glob("*.yaml")
        )

        self.assertNotIn("kind: Secret", yaml_text)
        self.assertNotIn("stringData:", yaml_text)
        self.assertIn("kind: ConfigMap", yaml_text)

    def test_candidate_test_is_non_destructive_to_reference(self):
        readme = (BACKEND / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("systemctl --user stop todo-backend.service", readme)
        self.assertNotIn("podman rm todo-backend", readme)
        self.assertIn("podman secret rm todo-kube-backend-secret", readme)
        self.assertIn("curl --fail http://127.0.0.1:8080/ready", readme)


class NginxKubeCandidateTests(unittest.TestCase):
    def test_candidate_uses_isolated_ports_and_stable_hostname(self):
        manifest = (NGINX / "nginx.yaml").read_text(encoding="utf-8")
        config = (NGINX / "config-lab.yaml").read_text(encoding="utf-8")
        unit = (NGINX / "todo-kube-nginx.kube").read_text(encoding="utf-8")

        self.assertIn("name: todo-kube-nginx", manifest)
        self.assertNotIn("hostPort:", manifest)
        self.assertIn("PublishPort=127.0.0.1:18080:8080", unit)
        self.assertIn("PublishPort=127.0.0.1:18443:8443", unit)
        self.assertIn("TODO_TLS_HOSTNAME: todo.test", config)
        self.assertIn("server_name todo.test;", config)

    def test_candidate_preserves_proxy_routes_and_headers(self):
        config = (NGINX / "config-lab.yaml").read_text(encoding="utf-8")

        self.assertIn("server todo-backend:8000;", config)
        self.assertIn("server todo-keycloak:8080;", config)
        for route in ("/auth/", "/api/", "/health", "/ready"):
            self.assertIn(route, config)
        self.assertIn("/etc/nginx/todo-proxy-headers.conf", config)

    def test_candidate_tls_volume_is_persistent_and_owned(self):
        manifest = (NGINX / "nginx.yaml").read_text(encoding="utf-8")
        readme = (NGINX / "README.md").read_text(encoding="utf-8")

        self.assertIn("kind: PersistentVolumeClaim", manifest)
        self.assertIn("name: todo-kube-nginx-data", manifest)
        self.assertIn('volume.podman.io/uid: "101"', manifest)
        self.assertIn('volume.podman.io/gid: "101"', manifest)
        self.assertNotIn("KubeDownForce=true", readme)
        self.assertIn("podman volume rm todo-kube-nginx-data", readme)

    def test_candidate_security_lifecycle_and_inputs_are_explicit(self):
        manifest = (NGINX / "nginx.yaml").read_text(encoding="utf-8")
        unit = (NGINX / "todo-kube-nginx.kube").read_text(encoding="utf-8")

        self.assertIn("runAsUser: 101", manifest)
        self.assertIn("runAsGroup: 101", manifest)
        self.assertIn("allowPrivilegeEscalation: false", manifest)
        self.assertIn("memory: 128Mi", manifest)
        self.assertNotIn("cpu:", manifest)
        self.assertIn("ConfigMap=config-lab.yaml", unit)
        self.assertIn("Network=todo.network", unit)
        self.assertIn("ExitCodePropagation=any", unit)
        self.assertIn("Restart=on-failure", unit)


class KeycloakKubeCandidateTests(unittest.TestCase):
    def test_candidate_uses_explicit_cluster_configuration(self):
        config = (KEYCLOAK / "config-lab.yaml").read_text(encoding="utf-8")

        self.assertIn("KC_CACHE: ispn", config)
        self.assertIn("KC_CACHE_STACK: jdbc-ping", config)
        self.assertIn(
            "KC_SPI_CACHE_EMBEDDED__DEFAULT__NODE_NAME: todo-kube-keycloak",
            config,
        )
        self.assertIn("KC_DB_URL_HOST: todo-postgres", config)
        self.assertIn("KC_DB_SCHEMA: keycloak", config)

    def test_candidate_uses_external_environment_secrets(self):
        manifest = (KEYCLOAK / "keycloak.yaml").read_text(encoding="utf-8")
        all_yaml = "\n".join(
            path.read_text(encoding="utf-8")
            for path in KEYCLOAK.glob("*.yaml")
        )

        self.assertIn("name: KCRAW_DB_PASSWORD", manifest)
        self.assertIn("name: KCRAW_BOOTSTRAP_ADMIN_PASSWORD", manifest)
        self.assertIn("secretKeyRef:", manifest)
        self.assertIn("name: todo-kube-keycloak-secret", manifest)
        self.assertNotIn("kind: Secret", all_yaml)
        self.assertNotIn("stringData:", all_yaml)

    def test_candidate_health_uses_tools_present_in_image(self):
        manifest = (KEYCLOAK / "keycloak.yaml").read_text(encoding="utf-8")

        self.assertIn("/usr/bin/bash", manifest)
        self.assertIn("/dev/tcp/127.0.0.1/9000", manifest)
        self.assertIn("/auth/health/live", manifest)
        self.assertIn("GET /auth/health/live HTTP/1.1", manifest)
        self.assertIn("timeout 2 grep", manifest)
        self.assertNotIn("curl", manifest)
        self.assertNotIn("wget", manifest)
        self.assertNotIn("tcpSocket:", manifest)

        guide = (KEYCLOAK / "README.md").read_text(encoding="utf-8")
        self.assertIn("/auth/health/ready", guide)

    def test_candidate_preserves_image_identity_and_limits(self):
        manifest = (KEYCLOAK / "keycloak.yaml").read_text(encoding="utf-8")

        self.assertIn("runAsUser: 1000", manifest)
        self.assertIn("runAsGroup: 0", manifest)
        self.assertIn("allowPrivilegeEscalation: false", manifest)
        self.assertIn("memory: 1Gi", manifest)
        self.assertNotIn("cpu:", manifest)
        self.assertIn("io.podman.annotations.pids-limit/keycloak", manifest)

    def test_candidate_systemd_lifecycle_is_explicit(self):
        unit = (KEYCLOAK / "todo-kube-keycloak.kube").read_text(
            encoding="utf-8"
        )

        self.assertIn("ConfigMap=config-lab.yaml", unit)
        self.assertIn("Network=todo.network", unit)
        self.assertIn("ExitCodePropagation=any", unit)
        self.assertIn("LogDriver=journald", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("TimeoutStopSec=60", unit)


class PostgresKubeCandidateTests(unittest.TestCase):
    def test_candidate_is_isolated_from_accepted_database_state(self):
        manifest = (POSTGRES / "postgres.yaml").read_text(encoding="utf-8")
        unit = (POSTGRES / "todo-kube-postgres.kube").read_text(
            encoding="utf-8"
        )

        self.assertIn("name: todo-kube-postgres", manifest)
        self.assertIn("name: todo-kube-postgres-data", manifest)
        self.assertNotIn("todo-postgres-data", manifest)
        self.assertIn("PublishPort=127.0.0.1:15432:5432", unit)
        self.assertNotIn("PublishPort=127.0.0.1:5432:5432", unit)

    def test_candidate_uses_pinned_image_and_external_inputs(self):
        manifest = (POSTGRES / "postgres.yaml").read_text(encoding="utf-8")
        config = (POSTGRES / "config-lab.yaml").read_text(encoding="utf-8")
        all_yaml = "\n".join(
            path.read_text(encoding="utf-8")
            for path in POSTGRES.glob("*.yaml")
        )

        self.assertIn("image: docker.io/library/postgres:17.11", manifest)
        self.assertIn("imagePullPolicy: Never", manifest)
        self.assertIn("configMapRef:", manifest)
        self.assertIn("name: todo-postgres-config", manifest)
        self.assertIn("POSTGRES_DB: todo", config)
        self.assertIn("POSTGRES_USER: todo", config)
        self.assertIn("secretName: todo-kube-postgres-secret", manifest)
        self.assertIn("POSTGRES_PASSWORD_FILE", manifest)
        self.assertNotIn("kind: Secret", all_yaml)
        self.assertNotIn("stringData:", all_yaml)

    def test_candidate_volume_has_explicit_postgres_ownership(self):
        manifest = (POSTGRES / "postgres.yaml").read_text(encoding="utf-8")
        readme = (POSTGRES / "README.md").read_text(encoding="utf-8")

        self.assertIn("kind: PersistentVolumeClaim", manifest)
        self.assertIn('volume.podman.io/uid: "999"', manifest)
        self.assertIn('volume.podman.io/gid: "999"', manifest)
        self.assertIn("mountPath: /var/lib/postgresql/data", manifest)
        self.assertIn("podman unshare", readme)
        self.assertIn("podman volume rm todo-kube-postgres-data", readme)

    def test_candidate_security_health_and_resources_are_explicit(self):
        manifest = (POSTGRES / "postgres.yaml").read_text(encoding="utf-8")

        self.assertIn("restartPolicy: Never", manifest)
        self.assertIn("runAsUser: 999", manifest)
        self.assertIn("runAsGroup: 999", manifest)
        self.assertIn("allowPrivilegeEscalation: false", manifest)
        self.assertIn("capabilities:\n          drop:\n            - ALL", manifest)
        self.assertIn("memory: 512Mi", manifest)
        self.assertNotIn("cpu:", manifest)
        self.assertIn("livenessProbe:\n        exec:", manifest)
        self.assertIn("- pg_isready", manifest)

    def test_candidate_systemd_lifecycle_is_explicit(self):
        unit = (POSTGRES / "todo-kube-postgres.kube").read_text(
            encoding="utf-8"
        )

        self.assertIn("Yaml=postgres.yaml", unit)
        self.assertIn("ConfigMap=config-lab.yaml", unit)
        self.assertIn("Network=todo.network", unit)
        self.assertIn("ExitCodePropagation=any", unit)
        self.assertIn("LogDriver=journald", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("TimeoutStopSec=60", unit)

    def test_candidate_test_preserves_accepted_runtime(self):
        readme = (POSTGRES / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("systemctl --user stop todo-postgres.service", readme)
        self.assertNotIn("podman volume rm todo-postgres-data", readme)
        self.assertIn("podman kill todo-kube-postgres-postgres", readme)
        self.assertIn("definitely-wrong-password", readme)
        self.assertIn("system_identifier FROM pg_control_system()", readme)
        self.assertIn("survives-container-recreation", readme)
        self.assertIn("curl --fail http://127.0.0.1:8080/ready", readme)


class ReplicationKubeCandidateTests(unittest.TestCase):
    def test_candidate_uses_isolated_names_ports_and_volumes(self):
        primary = (REPLICATION / "primary.yaml").read_text(encoding="utf-8")
        standby = (REPLICATION / "standby.yaml").read_text(encoding="utf-8")
        units = "\n".join(
            path.read_text(encoding="utf-8")
            for path in REPLICATION.glob("*.kube")
        )

        self.assertIn("name: todo-kube-repl-primary", primary)
        self.assertIn("name: todo-kube-repl-standby", standby)
        self.assertIn("todo-kube-repl-primary-data", primary)
        self.assertIn("todo-kube-repl-standby-data", standby)
        self.assertNotIn("todo-postgres-data", primary + standby)
        self.assertIn("192.168.0.108:15432:5432", units)
        self.assertIn("127.0.0.1:15433:5432", units)
        self.assertNotIn("PublishPort=127.0.0.1:5432:5432", units)

    def test_primary_creates_only_candidate_replication_contract(self):
        manifest = (REPLICATION / "primary.yaml").read_text(encoding="utf-8")
        config = (REPLICATION / "primary-config-lab.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("todo_kube_replicator", config)
        self.assertIn("LOGIN REPLICATION NOSUPERUSER", config)
        self.assertIn("NOINHERIT", config)
        self.assertIn("TODO_REPLICATION_HBA_CIDR", config)
        self.assertIn("scram-sha-256", config)
        self.assertIn("max_slot_wal_keep_size=1GB", manifest)
        self.assertNotIn("todo_replicator", config)
        self.assertNotIn("todo_standby", config)

    def test_standby_init_is_destructive_only_for_empty_volume(self):
        standby = (REPLICATION / "standby.yaml").read_text(encoding="utf-8")

        self.assertIn("initContainers:", standby)
        self.assertIn("pg_basebackup", standby)
        self.assertIn("--write-recovery-conf", standby)
        self.assertIn("--create-slot", standby)
        self.assertIn("--slot=\"$TODO_REPLICATION_SLOT\"", standby)
        self.assertIn('test -s "$PGDATA/PG_VERSION"', standby)
        self.assertIn("base backup skipped", standby)
        self.assertIn("Refusing non-empty partial standby data", standby)
        self.assertNotIn("rm -rf", standby)

    def test_standby_runtime_uses_slot_and_secret_passfile(self):
        standby = (REPLICATION / "standby.yaml").read_text(encoding="utf-8")
        config = (REPLICATION / "standby-config-lab.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("primary_conninfo=", standby)
        self.assertIn("primary_slot_name=$TODO_REPLICATION_SLOT", standby)
        self.assertIn("passfile=$pgpass", standby)
        self.assertIn("umask 077", standby)
        self.assertIn("TODO_PRIMARY_HOST: 192.168.0.108", config)
        self.assertIn("TODO_REPLICATION_SLOT: todo_kube_standby", config)

    def test_secrets_are_external_and_volumes_are_owned(self):
        manifests = "\n".join(
            path.read_text(encoding="utf-8")
            for path in REPLICATION.glob("*.yaml")
        )

        self.assertNotIn("kind: Secret", manifests)
        self.assertNotIn("stringData:", manifests)
        self.assertIn("secretName: todo-kube-repl-primary-secret", manifests)
        self.assertIn("secretName: todo-kube-repl-standby-secret", manifests)
        self.assertEqual(manifests.count('volume.podman.io/uid: "999"'), 2)
        self.assertEqual(manifests.count('volume.podman.io/gid: "999"'), 2)

    def test_systemd_owns_restart_and_propagates_failures(self):
        for path in REPLICATION.glob("*.kube"):
            unit = path.read_text(encoding="utf-8")
            self.assertIn("ExitCodePropagation=any", unit)
            self.assertIn("LogDriver=journald", unit)
            self.assertIn("Restart=on-failure", unit)
            self.assertIn("StartLimitBurst=3", unit)
            self.assertIn("TimeoutStopSec=60", unit)

        manifests = "\n".join(
            path.read_text(encoding="utf-8")
            for path in REPLICATION.glob("*.yaml")
        )
        self.assertEqual(manifests.count("restartPolicy: Never"), 2)
        self.assertNotIn("cpu:", manifests)


if __name__ == "__main__":
    unittest.main()
