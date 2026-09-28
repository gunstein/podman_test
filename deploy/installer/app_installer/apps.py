"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass

from . import settings, stack


@dataclass(frozen=True)
class App:
    """One web application: its public hostname, OAuth client and database.

    Every resource name comes from the application name through
    stack.Database, so the "todo" app owns todo-postgres, todo-app.service,
    todo-db-password and so on; the methods below forward to self.database
    for that. An App is not a database: code that works on the replicated
    database group takes REPLICATED_DATABASES, which holds only Databases.
    Build one with keyword arguments
    only (tests/test_apps.py checks it): the string fields are easy to mix
    up, and the hosts' Python 3.9 has no dataclass kw_only.
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
# The replication CA (key, certificate), shared by both hosts; see replication_tls.py.
REPLICATION_CA_SECRETS = ("replication-ca-key", "replication-ca-cert")
# The DR group: every app's database, then Keycloak's. Bootstrap, promotion,
# backup and rebuild always act on all of them together.
REPLICATED_DATABASES = tuple(app.database for app in APPS) + (KEYCLOAK_DATABASE,)


def services(applications=None, *, databases=True):
    """User systemd services in stop order: proxy, apps, Keycloak, then databases.

    Callers stop the serving tier before the databases it uses. Starting goes
    the other way round, databases first (install.install).

    applications limits the list to some apps (default: all). With
    databases=False only the serving tier is returned, which is what a
    database-only standby must not run.
    """
    selected = APPS if applications is None else applications
    return ['shared-proxy.service', *[app.service('app') for app in selected], 'keycloak.service'] + (
        [app.service('postgres') for app in selected] + [KEYCLOAK_DATABASE.service('postgres')]
        if databases else [])
