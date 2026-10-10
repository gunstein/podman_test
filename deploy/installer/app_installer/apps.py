"""An installation's model: its apps (App), their workloads and the Platform that holds them.

platform_file.py makes a Platform from platform.yaml and the apps' app.yaml.
"""
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
    works on the replicated database group asks Platform.replicated_databases.
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
        """The REST collection the DR tools read to check the app answers, e.g. /api/todos.

        After a promotion, promoted.require_application expects a list there
        (a public read). nginx itself forwards all of /api/ to the backend.
        """
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

    def kube_secret(self, component: str) -> str:
        """The Kube secret a container of the app pod mounts, such as todo-kube-backend-secret."""
        return self.names.kube_secret(component)

    def image(self, component: str) -> str:
        """The app's image for component "backend" or "frontend"."""
        return self.names.image(component)

    def image_archive(self, component: str) -> str:
        return self.names.image_archive(component)


# The Keycloak client the realm import brings (keycloak/todo-realm.json): the
# template every other app's client is copied from (keycloak.configure).
TEMPLATE_CLIENT = "todo-frontend"
NETWORK = "app-network"
# Keycloak itself is the identity server, not a per-app/per-database resource,
# so its image does not follow the "<name>-<component>" naming.
KEYCLOAK_IMAGE = f"localhost/keycloak:{settings.IMAGE_TAG}"
KEYCLOAK_ARCHIVE = f"keycloak-{settings.IMAGE_TAG}.tar"

# The resources nginx, shared by every app, runs with; they belong to no app.
PROXY_IMAGE = f"localhost/platform-proxy:{settings.IMAGE_TAG}"
PROXY_ARCHIVE = f"platform-proxy-{settings.IMAGE_TAG}.tar"
# The TLS volume nginx used before its Podman secrets; kept for going back (tls.py).
NGINX_TLS_VOLUME = "platform-nginx-data"

# Keycloak has its own dedicated database (no frontend/backend/OAuth client of
# its own), replicated for DR parity alongside every registered Application.
KEYCLOAK_DATABASE = stack.Database(name="keycloak", replication_port=5434)
KEYCLOAK_KUBE_ADMIN_SECRET = "keycloak-kube-admin-secret"
KEYCLOAK_ADMIN_SECRET = "keycloak-admin-password"
# nginx's TLS files as Podman secrets on this host (tls_secrets.py), one secret
# per file: {file name: raw secret}. Host-local: the DR copy never carries them.
PROXY_TLS_SECRETS = {
    "tls-mode": "platform-proxy-tls-mode",              # local or provided
    "ca.crt": "platform-proxy-ca-cert",                 # the root clients trust
    "server.crt": "platform-proxy-tls-cert",            # nginx's certificate (+ chain)
    "server.key": "platform-proxy-tls-key",             # its private key
    "ca.key": "platform-proxy-ca-key",                  # the demo CA's key, local mode only
    "request.key": "platform-proxy-tls-request-key",    # a key waiting for its certificate
    "incoming.crt": "platform-proxy-tls-incoming",      # tls-install's certificate while it is checked
    "incoming-ca.crt": "platform-proxy-tls-incoming-ca",
}
# The Kube secret nginx mounts at /var/lib/platform-tls: tls-mode, ca.crt, server.crt
# and server.key, made from the raw secrets above; never a CA or waiting key.
PROXY_KUBE_TLS_SECRET = "platform-kube-proxy-tls-secret"
# The replication CA (key, certificate), shared by both hosts; see replication_tls.py.
REPLICATION_CA_SECRETS = ("replication-ca-key", "replication-ca-cert")


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


@dataclass(frozen=True)
class Platform:
    """One installation: its apps, in start order, and Keycloak's default hostname.

    Every part of the installer and the DR tools that acts on an
    installation takes one Platform and asks it, instead of reading a list
    kept in a module. A build reads it from platform.yaml (platform_file);
    an offline bundle carries its own in bundle.json (to_json, from_json),
    and so does a host it was installed on, so a host never rebuilds it.
    Keycloak's own database is the same for every platform (KEYCLOAK_DATABASE).
    """

    apps: tuple
    identity_hostname: str

    def __post_init__(self):
        if not self.apps:
            raise ValueError("A platform needs at least one app")
        # identity names Keycloak in --target-hostname and TARGET_IDENTITY_HOSTNAME (target_render.name_target).
        if any(app.name == "identity" for app in self.apps):
            raise ValueError("The app name identity is reserved for Keycloak's hostname")
        for field in ("name", "hostname", "keycloak_client", "replication_port"):
            values = [getattr(app, field) for app in self.apps]
            if len(values) != len(set(values)):
                raise ValueError(f"Two apps share a {field}: {values}")
        if KEYCLOAK_DATABASE.replication_port in (app.replication_port for app in self.apps):
            raise ValueError(f"Port {KEYCLOAK_DATABASE.replication_port} is Keycloak's database's")

    def app(self, name):
        """The app with this name."""
        for app in self.apps:
            if app.name == name:
                return app
        raise ValueError(f"No app named {name!r}; the apps are {', '.join(a.name for a in self.apps)}")

    def select(self, names):
        """This platform with only the named apps, in their order here; all of them if names is empty."""
        if not names:
            return self
        unknown = set(names) - {app.name for app in self.apps}
        if unknown:
            raise ValueError(f"Unknown apps: {', '.join(sorted(unknown))}")
        return Platform(apps=tuple(app for app in self.apps if app.name in names),
                        identity_hostname=self.identity_hostname)

    @property
    def replicated_databases(self):
        """The DR group: every app's database, then Keycloak's. Bootstrap, promotion,
        backup and rebuild always act on all of them together."""
        return tuple(app.database for app in self.apps) + (KEYCLOAK_DATABASE,)

    def workloads(self):
        """Every workload, in start order; stop goes the other way.

        Each app's database first, then Keycloak's, Keycloak, the apps, and
        nginx last: each starts after what it needs (deploy/quadlet/*.kube.j2
        say the same in Requires= and After=). A database is healthy before the
        next workload starts. An app shares its ConfigMap file with its database;
        nginx's ConfigMaps are in its own shared-proxy.yaml.
        """
        return (
            *(Workload(app.database.container, app.database.manifest, app.database.config_manifest,
                       wait_healthy=True) for app in self.apps),
            Workload(KEYCLOAK_DATABASE.container, KEYCLOAK_DATABASE.manifest, KEYCLOAK_DATABASE.config_manifest,
                     wait_healthy=True),
            Workload("keycloak", "keycloak.yaml"),
            *(Workload(app.pod, app.manifest, app.config_manifest) for app in self.apps),
            Workload("shared-proxy", "shared-proxy.yaml"),
        )

    def serving_workloads(self):
        """The serving tier in start order: workloads() without the databases.

        Keycloak, the apps and nginx: what a database-only standby must not run,
        and what the DR tools stop around a database restart and start again.
        """
        database_pods = {database.container for database in self.replicated_databases}
        return tuple(workload for workload in self.workloads() if workload.pod not in database_pods)

    def services(self, *, databases=True):
        """User systemd services in stop order, the reverse of workloads(): nginx first, the databases last.

        With databases=False only the serving tier is returned (serving_workloads()).
        """
        return [workload.service for workload in
                reversed(self.workloads() if databases else self.serving_workloads())]

    def ready(self, role):
        """What wait-ready.sh waits for on a host in role "app" or "standby": (pods, containers).

        An app host runs every workload; a database-only standby only the
        databases. The containers are the long-running ones: each database,
        Keycloak, each app's backend and frontend, and nginx (their names in
        deploy/manifests, kept by podman kube play --no-pod-prefix).
        """
        databases = [database.container for database in self.replicated_databases]
        if role == "standby":
            return databases, databases
        if role != "app":
            raise ValueError(f"role must be app or standby, not {role!r}")
        containers = [*databases, "keycloak",
                      *(app.names.resource(part) for app in self.apps for part in ("backend", "frontend")), "nginx"]
        return [workload.pod for workload in self.workloads()], containers

    def host_ports(self):
        """The host ports each container may publish, {container: ports}: preflight.sh checks they are free.

        Each database its replication port (a DR primary publishes it), nginx
        the fixed local HTTP and HTTPS ports it always binds on 127.0.0.1.
        """
        ports = {database.container: (database.replication_port,) for database in self.replicated_databases}
        return {**ports, "nginx": (settings.LOCAL_HTTP_PORT, settings.HTTPS_PORT)}

    def to_json(self):
        """This platform as plain data, for bundle.json and a host's record."""
        return {"identity_hostname": self.identity_hostname,
                "apps": [{"name": app.name, "hostname": app.hostname, "keycloak_client": app.keycloak_client,
                          "replication_port": app.replication_port, "api_collection": app.api_collection}
                         for app in self.apps]}

    @classmethod
    def from_json(cls, data):
        """The platform to_json wrote; anything else is a ValueError that says what is wrong."""
        fields = {"name": str, "hostname": str, "keycloak_client": str, "replication_port": int,
                  "api_collection": str}
        if not (isinstance(data, dict) and set(data) == {"identity_hostname", "apps"}
                and isinstance(data["identity_hostname"], str) and isinstance(data["apps"], list)):
            raise ValueError("A platform needs exactly identity_hostname and a list of apps")
        for entry in data["apps"]:
            if not (isinstance(entry, dict) and set(entry) == set(fields)
                    and all(type(entry[key]) is kind for key, kind in fields.items())):
                raise ValueError(f"An app needs exactly {', '.join(fields)}: {entry!r}")
        return cls(apps=tuple(App(**entry) for entry in data["apps"]),
                   identity_hostname=data["identity_hostname"])

