"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass

from . import stack


@dataclass(frozen=True)
class App:
    name: str
    chart: str
    hostname: str
    keycloak_client: str
    replication_port: int = 5432
    api_collection: str = ""

    @property
    def database(self) -> stack.Database:
        return stack.Database(self.name, self.chart, self.replication_port)

    def api_path(self) -> str:
        return "/api/" + (self.api_collection or self.name)

    def resource(self, component: str) -> str:
        return self.database.resource(component)

    def unit(self, component: str) -> str:
        return self.database.unit(component)

    def service(self, component: str) -> str:
        return self.database.service(component)

    def manifest(self, component: str) -> str:
        return self.database.manifest(component)

    def secret(self, role: str) -> str:
        return self.database.secret(role)

    def kube_secret(self, component: str) -> str:
        return self.database.kube_secret(component)

    def database_role(self, role: str) -> str:
        return self.database.database_role(role)

    def replication_slot(self, rebuilt: bool = False) -> str:
        return self.database.replication_slot(rebuilt)

    def replication_passfile(self) -> str:
        return self.database.replication_passfile()

    def volume(self, purpose: str) -> str:
        return self.database.volume(purpose)

    def legacy_volume_service(self, purpose: str) -> str:
        return self.database.legacy_volume_service(purpose)

    def image(self, component: str) -> str:
        return self.database.image(component)

    def image_archive(self, component: str) -> str:
        return self.database.image_archive(component)

    def source_directory(self, component: str) -> str:
        return self.resource(component)


APPS = (
    App(name="todo", chart="todo", hostname="todo.test", keycloak_client="todo-frontend", api_collection="todos"),
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

# Keycloak has its own dedicated database (no frontend/backend/OAuth client of
# its own), replicated for DR parity alongside every registered Application.
KEYCLOAK_DATABASE = stack.Database(name="keycloak", chart="keycloak", replication_port=5434)
KEYCLOAK_KUBE_ADMIN_SECRET = "keycloak-kube-admin-secret"
KEYCLOAK_ADMIN_SECRET = "keycloak-admin-password"
REPLICATED_DATABASES = tuple(app.database for app in REPLICATED_APPS) + (KEYCLOAK_DATABASE,)


def describe(workload):
    """Names and source files for transport-only Ansible bridges."""
    if isinstance(workload, stack.Database):
        return _describe_database(workload)
    return _describe_application(workload)


def _describe_database(database):
    return {
        "name": database.name,
        "postgres_unit": database.unit("postgres"),
        "raw_secrets": [database.secret(role) for role in ("db", "replicator")],
        "postgres_container": database.resource("postgres"),
        "postgres_service": database.service("postgres"),
        # The database-only entry's co-located workload is the shared identity pod.
        "application_service": "keycloak.service",
        "data_volume": database.volume("data"),
        "backup_volume": database.volume("backup"),
        "archive_check_prefix": database.database_role("m15_archive_check"),
        "replication_secret": database.secret("replicator"),
        "replication_role": database.database_role("replicator"),
        "replication_slot": database.replication_slot(),
        "rebuild_slot": database.replication_slot(rebuilt=True),
        "replication_port": database.replication_port,
        "postgres_image": database.image("postgres"),
        "postgres_archive": database.image_archive("postgres"),
        "templates": ["app-network.network", database.unit("postgres") + ".j2",
                      "keycloak.kube.j2", "shared-proxy.kube.j2"],
        "manifests": {"postgres": [database.manifest("postgres"), database.manifest("config")]},
    }


def _describe_application(app):
    identity = app == IDENTITY_DATABASE_APP
    return {
        "name": app.name,
        "hostname": app.hostname,
        "api_path": app.api_path(),
        "keycloak_client": app.keycloak_client,
        "application_unit": app.unit("app"),
        "postgres_unit": app.unit("postgres"),
        "raw_secrets": [app.secret(role) for role in ("db", "migrator", "app", "replicator")],
        "postgres_container": app.resource("postgres"),
        "postgres_service": app.service("postgres"),
        "application_service": app.service("app"),
        "data_volume": app.volume("data"),
        "backup_volume": app.volume("backup"),
        "archive_check_prefix": app.database_role("m15_archive_check"),
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
        [app.service('postgres') for app in selected] + [KEYCLOAK_DATABASE.service('postgres')]
        if databases else [])
