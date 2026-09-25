"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass

from . import settings, stack


@dataclass(frozen=True)
class App:
    """One web application: its public hostname, OAuth client and database.

    Every resource name comes from the application name through
    stack.Database, so the "todo" app owns todo-postgres, todo-app.service,
    todo-db-password and so on. The methods below that forward to
    self.database exist so callers can treat an App and a database-only
    workload (Keycloak's) the same way.
    """

    name: str
    hostname: str
    keycloak_client: str
    replication_port: int = 5432
    api_collection: str = ""

    @property
    def database(self) -> stack.Database:
        """The PostgreSQL workload this application owns."""
        return stack.Database(self.name, self.replication_port)

    def api_path(self) -> str:
        """The REST collection nginx routes to this app's backend, e.g. /api/todos."""
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

    def image(self, component: str) -> str:
        return self.database.image(component)

    def image_archive(self, component: str) -> str:
        return self.database.image_archive(component)


APPS = (
    App(name="todo", hostname="todo.test", keycloak_client="todo-frontend", api_collection="todos"),
    App(name="notes", hostname="notes.test", keycloak_client="notes-frontend", replication_port=5433),
)

# Keycloak now has its own dedicated database (KEYCLOAK_DATABASE below), not
# this app's. The name still backs every resource shared across the whole
# stack instead of being owned by any one app: the proxy image, the shared
# config.yaml, the nginx-data TLS volume and the default Keycloak client trust.
SHARED_RESOURCE_OWNER = APPS[0]
NETWORK = "app-network"
# Keycloak itself is the identity server, not a per-app/per-database resource,
# so it does not go through Database.image()'s "<name>-<component>" naming.
KEYCLOAK_IMAGE = f"localhost/keycloak:{settings.IMAGE_TAG}"
KEYCLOAK_ARCHIVE = f"keycloak-{settings.IMAGE_TAG}.tar"
PROXY_IMAGE = SHARED_RESOURCE_OWNER.image("proxy")
PROXY_ARCHIVE = SHARED_RESOURCE_OWNER.image_archive("proxy")

# Keycloak has its own dedicated database (no frontend/backend/OAuth client of
# its own), replicated for DR parity alongside every registered Application.
KEYCLOAK_DATABASE = stack.Database(name="keycloak", replication_port=5434)
KEYCLOAK_KUBE_ADMIN_SECRET = "keycloak-kube-admin-secret"
KEYCLOAK_ADMIN_SECRET = "keycloak-admin-password"
# Keep todo/notes as Apps here, not Databases: describe() dispatches on
# isinstance(workload, stack.Database), and App already forwards every
# Database-shaped method a database-only consumer needs.
REPLICATED_DATABASES = APPS + (KEYCLOAK_DATABASE,)


def describe(workload):
    """Every name and file one replicated database needs, as a plain dict.

    The DR tools (the Ansible roles, app_ops and the replication-apps command)
    read names from here rather than rebuilding them, so a new app only needs
    an entry in APPS. Keycloak's database has no application of its own, so
    it gets a shorter entry that also carries the Keycloak admin secret.
    """
    if isinstance(workload, stack.Database):
        return _describe_database(workload)
    return _describe_application(workload)


def _describe_database(database):
    raw_secrets = [database.secret(role) for role in ("db", "replicator")]
    if database == KEYCLOAK_DATABASE:
        # Keycloak's own admin credential has no App/frontend to carry it;
        # sync it here so the promoted host can still install_keycloak.
        raw_secrets.append(KEYCLOAK_ADMIN_SECRET)
    return {
        "name": database.name,
        "postgres_unit": database.unit("postgres"),
        "raw_secrets": raw_secrets,
        "postgres_container": database.resource("postgres"),
        "postgres_service": database.service("postgres"),
        # The database-only entry's co-located workload is the shared identity pod.
        "application_service": "keycloak.service",
        "data_volume": database.volume("data"),
        "backup_volume": database.volume("backup"),
        "archive_check_prefix": database.database_role("archive_check"),
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
    owns_shared_resources = app == SHARED_RESOURCE_OWNER
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
        "archive_check_prefix": app.database_role("archive_check"),
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
                           + (["keycloak.yaml"] if owns_shared_resources else []),
            "keycloak": ["keycloak.yaml"],
            "shared-proxy": ["shared-proxy.yaml", SHARED_RESOURCE_OWNER.manifest("config")],
        },
    }


def services(applications=None, *, databases=True):
    """User systemd services in start order: proxy, apps, Keycloak, then databases.

    applications limits the list to some apps (default: all). With
    databases=False only the serving tier is returned, which is what a
    database-only standby must not run.
    """
    selected = APPS if applications is None else applications
    return ['shared-proxy.service', *[app.service('app') for app in selected], 'keycloak.service'] + (
        [app.service('postgres') for app in selected] + [KEYCLOAK_DATABASE.service('postgres')]
        if databases else [])
