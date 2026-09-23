"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass


@dataclass(frozen=True)
class App:
    name: str
    chart: str
    hostname: str
    keycloak_client: str
    replication_port: int = 5432

    def resource(self, component: str) -> str:
        return f"{self.name}-{component}"

    def unit(self, component: str) -> str:
        return self.resource(component) + ".kube"

    def service(self, component: str) -> str:
        return self.resource(component) + ".service"

    def manifest(self, component: str) -> str:
        # Preserve the canonical Todo bundle filenames used by existing DR callers.
        stem = component if self.name == "todo" else self.resource(component)
        return stem + ".yaml"

    def secret(self, role: str) -> str:
        return self.resource(role) + "-password"

    def kube_secret(self, component: str) -> str:
        return self.resource("kube-" + component) + "-secret"

    def database_role(self, role: str) -> str:
        return f"{self.name}_{role}"

    def replication_slot(self, rebuilt: bool = False) -> str:
        return self.database_role("rebuilt_standby" if rebuilt else "standby")

    def replication_passfile(self) -> str:
        return "." + self.resource("replication") + ".pgpass"

    def volume(self, purpose: str) -> str:
        return self.resource("postgres-" + purpose)

    def legacy_volume_service(self, purpose: str) -> str:
        return self.volume(purpose) + "-volume"

    def image(self, component: str) -> str:
        if component == "postgres":
            return "docker.io/library/postgres:17.11"
        return "localhost/" + self.resource(component) + ":m12"

    def image_archive(self, component: str) -> str:
        if component == "postgres":
            return "postgres-17.11.tar"
        return self.resource(component) + "-m12.tar"

    def source_directory(self, component: str) -> str:
        return component if self.name == "todo" else self.resource(component)


APPS = (
    App(name="todo", chart="todo", hostname="todo.test", keycloak_client="todo-frontend"),
    App(name="notes", chart="notes", hostname="notes.test", keycloak_client="notes-frontend", replication_port=5433),
)

# The existing Todo database hosts the shared realm; preserve its stored credentials.
IDENTITY_DATABASE_APP = APPS[0]
NETWORK = "app-network"
KEYCLOAK_IMAGE = "localhost/keycloak:m12"
KEYCLOAK_ARCHIVE = "keycloak-m12.tar"
PROXY_IMAGE = IDENTITY_DATABASE_APP.image("proxy")
PROXY_ARCHIVE = IDENTITY_DATABASE_APP.image_archive("proxy")

# Todo-only bridge and guarded promotion passed live two-host checkpoints first.
REPLICATED_APPS = APPS


def describe(app):
    """Names and source files for transport-only Ansible bridges."""
    identity = app == IDENTITY_DATABASE_APP
    return {
        "name": app.name,
        "hostname": app.hostname,
        "keycloak_client": app.keycloak_client,
        "application_unit": app.unit("app"),
        "postgres_unit": app.unit("postgres"),
        "raw_secrets": [app.secret(role) for role in ("db", "migrator", "app", "replicator")]
                       + ([app.secret("keycloak-db"), app.secret("keycloak-admin")] if identity else []),
        "postgres_container": app.resource("postgres"),
        "postgres_service": app.service("postgres"),
        "application_service": app.service("app"),
        "data_volume": app.volume("data"),
        "backup_volume": app.volume("backup"),
        "replication_secret": app.secret("replicator"),
        "replication_role": app.database_role("replicator"),
        "replication_slot": app.replication_slot(),
        "rebuild_slot": app.replication_slot(rebuilt=True),
        "replication_port": app.replication_port,
        "postgres_image": app.image("postgres"),
        "postgres_archive": app.image_archive("postgres"),
        "templates": ["app-network.network", app.unit("postgres") + ".j2",
                      app.unit("app") + ".j2", "keycloak.kube.j2", "shared-proxy.kube.j2"],
        "manifests": {
            "postgres": [app.manifest("postgres"), app.manifest("config")],
            "application": [app.manifest("app"), app.manifest("config")]
                           + (["keycloak.yaml"] if identity else []),
            "keycloak": ["keycloak.yaml"],
            "shared-proxy": ["shared-proxy.yaml", IDENTITY_DATABASE_APP.manifest("config")],
        },
    }


def services(applications=None, *, databases=True):
    selected = REPLICATED_APPS if applications is None else applications
    return ['shared-proxy.service', *[app.service('app') for app in selected], 'keycloak.service'] + (
        [app.service('postgres') for app in selected] if databases else [])
