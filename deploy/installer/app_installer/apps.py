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

# The app whose public hostname is also Keycloak's and the OIDC issuer's
# (TARGET_EXTERNAL_HOSTNAME). Its Keycloak client, from the realm import, is
# the template the other apps' clients are copied from (keycloak.configure),
# and nginx's certificate names its hostname first. An install always
# includes it. Keycloak's own database is KEYCLOAK_DATABASE below.
IDENTITY_APP = APPS[0]
NETWORK = "app-network"
# Keycloak itself is the identity server, not a per-app/per-database resource,
# so its image does not follow the "<name>-<component>" naming.
KEYCLOAK_IMAGE = f"localhost/keycloak:{settings.IMAGE_TAG}"
KEYCLOAK_ARCHIVE = f"keycloak-{settings.IMAGE_TAG}.tar"

# The resources nginx, shared by every app, runs with. Their names start with
# "todo-" because todo was the first app; they belong to no app.
PROXY_IMAGE = f"localhost/todo-proxy:{settings.IMAGE_TAG}"
PROXY_ARCHIVE = f"todo-proxy-{settings.IMAGE_TAG}.tar"
# The ConfigMap file shared-proxy.kube names (ConfigMap=); it is the identity
# app's own ConfigMap file (tests/test_kube_name_contract.py).
PROXY_CONFIG_MANIFEST = "config.yaml"
# The TLS volume nginx used before its Podman secrets; kept for going back (tls.py).
NGINX_TLS_VOLUME = "todo-nginx-data"

# Keycloak has its own dedicated database (no frontend/backend/OAuth client of
# its own), replicated for DR parity alongside every registered Application.
KEYCLOAK_DATABASE = stack.Database(name="keycloak", replication_port=5434)
KEYCLOAK_KUBE_ADMIN_SECRET = "keycloak-kube-admin-secret"
KEYCLOAK_ADMIN_SECRET = "keycloak-admin-password"
# nginx's TLS files as Podman secrets on this host (tls_secrets.py), one secret
# per file: {file name: raw secret}. Host-local: the DR copy never carries them.
PROXY_TLS_SECRETS = {
    "tls-mode": "todo-proxy-tls-mode",              # local or provided
    "ca.crt": "todo-proxy-ca-cert",                 # the root clients trust
    "server.crt": "todo-proxy-tls-cert",            # nginx's certificate (+ chain)
    "server.key": "todo-proxy-tls-key",             # its private key
    "ca.key": "todo-proxy-ca-key",                  # the demo CA's key, local mode only
    "request.key": "todo-proxy-tls-request-key",    # a key waiting for its certificate
    "incoming.crt": "todo-proxy-tls-incoming",      # tls-install's certificate while it is checked
    "incoming-ca.crt": "todo-proxy-tls-incoming-ca",
}
# The Kube secret nginx mounts at /var/lib/todo-tls: tls-mode, ca.crt, server.crt
# and server.key, made from the raw secrets above; never a CA or waiting key.
PROXY_KUBE_TLS_SECRET = "todo-kube-proxy-tls-secret"
# The replication CA (key, certificate), shared by both hosts; see replication_tls.py.
REPLICATION_CA_SECRETS = ("replication-ca-key", "replication-ca-cert")
# The DR group: every app's database, then Keycloak's. Bootstrap, promotion,
# backup and rebuild always act on all of them together.
REPLICATED_DATABASES = tuple(app.database for app in APPS) + (KEYCLOAK_DATABASE,)


@dataclass(frozen=True)
class Workload:
    """One pod, run from one Kube YAML file by one Quadlet unit.

    pod is also the unit's and the service's base name: todo-postgres runs
    from todo-postgres.kube as todo-postgres.service. yaml is the unit's
    Yaml= file, config its ConfigMap= file ("" for none). wait_healthy: a
    start waits until the pod is healthy before the next one starts.
    """

    pod: str
    yaml: str
    config: str = ""
    wait_healthy: bool = False

    @property
    def unit(self) -> str:
        return self.pod + ".kube"

    @property
    def service(self) -> str:
        return self.pod + ".service"

    @property
    def manifests(self) -> tuple:
        """Its Kube YAML files: the pod's, then its ConfigMap's if it has one."""
        return (self.yaml, self.config) if self.config else (self.yaml,)


def workloads(applications=APPS):
    """Every workload of these apps, in start order; stop goes the other way.

    Each app's database first, then Keycloak's, Keycloak, the apps, and
    nginx last: each starts after what it needs (deploy/quadlet/*.kube.j2
    say the same in Requires= and After=). A database is healthy before the
    next workload starts. An app shares its ConfigMap file with its database,
    and nginx's unit names the identity app's (PROXY_CONFIG_MANIFEST).
    """
    return (
        *(Workload(app.database.container, app.database.manifest, app.database.config_manifest,
                   wait_healthy=True) for app in applications),
        Workload(KEYCLOAK_DATABASE.container, KEYCLOAK_DATABASE.manifest, KEYCLOAK_DATABASE.config_manifest,
                 wait_healthy=True),
        Workload("keycloak", "keycloak.yaml"),
        *(Workload(app.pod, app.manifest, app.config_manifest) for app in applications),
        Workload("shared-proxy", "shared-proxy.yaml", PROXY_CONFIG_MANIFEST),
    )


def services(applications=None, *, databases=True):
    """User systemd services in stop order, the reverse of workloads(): nginx first, the databases last.

    applications limits the list to some apps (default: all). With
    databases=False only the serving tier is returned, which is what a
    database-only standby must not run.
    """
    database_pods = {database.container for database in REPLICATED_DATABASES}
    return [workload.service for workload in reversed(workloads(APPS if applications is None else applications))
            if databases or workload.pod not in database_pods]
