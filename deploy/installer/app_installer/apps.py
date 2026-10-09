"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass

from . import settings, stack


@dataclass(frozen=True)
class App:
    """One web application: its public hostname, OAuth client and database.

    Its resource names come from its name through stack.Names, so the "todo"
    app runs the todo-app pod (unit todo-app.kube, service todo-app.service)
    from the images localhost/todo-backend and localhost/todo-frontend.
    Its PostgreSQL workload is self.database (todo-postgres, todo-db-password,
    the todo_migrator role and so on). An App is not a database: code that
    works on the replicated database group takes REPLICATED_DATABASES.
    Build an App with keyword arguments only (tests/test_apps.py checks it):
    the string fields are easy to mix up, and the hosts' Python 3.9 has no
    dataclass kw_only.
    """

    name: str
    hostname: str
    keycloak_client: str
    replication_port: int = 5432
    api_collection: str = ""

    @property
    def names(self) -> stack.Names:
        """The naming rules for this app's name."""
        return stack.Names(self.name)

    @property
    def database(self) -> stack.Database:
        """The PostgreSQL workload this application owns."""
        return stack.Database(self.name, self.replication_port)

    def api_path(self) -> str:
        """The REST collection nginx routes to this app's backend, e.g. /api/todos."""
        return "/api/" + (self.api_collection or self.name)

    # The app pod: migration, backend and frontend.

    @property
    def pod(self) -> str:
        return self.names.resource("app")

    @property
    def unit(self) -> str:
        return self.names.unit("app")

    @property
    def service(self) -> str:
        return self.names.service("app")

    @property
    def manifest(self) -> str:
        return self.names.manifest("app")

    @property
    def config_manifest(self) -> str:
        """The ConfigMap file, shared with the database pod (self.database.config_manifest)."""
        return self.names.manifest("config")

    def image(self, component: str) -> str:
        """The app's image for component "backend" or "frontend"."""
        return self.names.image(component)

    def image_archive(self, component: str) -> str:
        return self.names.image_archive(component)


APPS = (
    App(name="todo", hostname="todo.test", keycloak_client="todo-frontend", api_collection="todos"),
    App(name="notes", hostname="notes.test", keycloak_client="notes-frontend", replication_port=5433),
)

# The app whose name the resources shared by the whole stack carry: the proxy
# image, the shared config.yaml and the nginx-data TLS volume. Its public
# hostname is also Keycloak's and the OIDC issuer's (TARGET_EXTERNAL_HOSTNAME),
# and its Keycloak client, from the realm import, is the template the other
# apps' clients are copied from (keycloak.configure).
# Keycloak's own database is KEYCLOAK_DATABASE below, not this app's.
SHARED_RESOURCE_OWNER = APPS[0]
NETWORK = "app-network"
# Keycloak itself is the identity server, not a per-app/per-database resource,
# so its image does not follow the "<name>-<component>" naming.
KEYCLOAK_IMAGE = f"localhost/keycloak:{settings.IMAGE_TAG}"
KEYCLOAK_ARCHIVE = f"keycloak-{settings.IMAGE_TAG}.tar"
PROXY_IMAGE = SHARED_RESOURCE_OWNER.names.image("proxy")
PROXY_ARCHIVE = SHARED_RESOURCE_OWNER.names.image_archive("proxy")

# Keycloak has its own dedicated database (no frontend/backend/OAuth client of
# its own), replicated for DR parity alongside every registered Application.
KEYCLOAK_DATABASE = stack.Database(name="keycloak", replication_port=5434)
KEYCLOAK_KUBE_ADMIN_SECRET = "keycloak-kube-admin-secret"
KEYCLOAK_ADMIN_SECRET = "keycloak-admin-password"
# nginx's TLS files as Podman secrets on this host (tls_secrets.py), one secret
# per file: {file name: raw secret}. Host-local: the DR copy never carries them.
PROXY_TLS_SECRETS = {name: SHARED_RESOURCE_OWNER.names.resource(component) for name, component in (
    ("tls-mode", "proxy-tls-mode"),           # local or provided
    ("ca.crt", "proxy-ca-cert"),              # the root clients trust
    ("server.crt", "proxy-tls-cert"),         # nginx's certificate (+ chain)
    ("server.key", "proxy-tls-key"),          # its private key
    ("ca.key", "proxy-ca-key"),               # the demo CA's key, local mode only
    ("request.key", "proxy-tls-request-key"),  # a key waiting for its certificate
    ("incoming.crt", "proxy-tls-incoming"),   # tls-install's certificate while it is checked
    ("incoming-ca.crt", "proxy-tls-incoming-ca"),
)}
# The Kube secret nginx mounts at /var/lib/todo-tls: tls-mode, ca.crt, server.crt
# and server.key, made from the raw secrets above; never a CA or waiting key.
PROXY_KUBE_TLS_SECRET = SHARED_RESOURCE_OWNER.names.kube_secret("proxy-tls")
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
    return ['shared-proxy.service', *[app.service for app in selected], 'keycloak.service'] + (
        [app.database.service for app in selected] + [KEYCLOAK_DATABASE.service]
        if databases else [])
