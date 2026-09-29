"""Naming rules and the PostgreSQL workload, shared by the application registry.

Names holds only the rules that turn one name into resource names: the
"todo" name gives todo-app, todo-app.kube, todo-app.service, and so on.
Database is one PostgreSQL workload: its pod, its data volumes, its roles and
passwords, and its replication. An app (apps.App) and a database each use
Names for their own resources; neither pretends to be the other.
"""
from dataclasses import dataclass

from . import settings


@dataclass(frozen=True)
class Names:
    """How everything that shares one name is named: the name, a dash, a component.

    For name="notes": resource("app") -> notes-app, unit("app") ->
    notes-app.kube, service("app") -> notes-app.service, manifest("app") ->
    notes-app.yaml, image("backend") -> localhost/notes-backend:m12. Keeping
    every rule here means no part of the system can name a resource
    differently from another.
    """

    name: str

    def resource(self, component: str) -> str:
        return f"{self.name}-{component}"

    def unit(self, component: str) -> str:
        return self.resource(component) + ".kube"

    def service(self, component: str) -> str:
        return self.resource(component) + ".service"

    def manifest(self, component: str) -> str:
        # Todo was the only application before Notes/Keycloak's own database
        # existed, so its manifests are still the bare component name
        # ("postgres.yaml", not "todo-postgres.yaml"); existing DR callers and
        # packaged bundles depend on that exact filename, so it stays fixed.
        stem = component if self.name == "todo" else self.resource(component)
        return stem + ".yaml"

    def kube_secret(self, component: str) -> str:
        return self.resource("kube-" + component) + "-secret"

    def image(self, component: str) -> str:
        """A locally built image, such as localhost/todo-backend:m12."""
        return f"localhost/{self.resource(component)}:{settings.IMAGE_TAG}"

    def image_archive(self, component: str) -> str:
        """The file name of a locally built image in the offline bundle."""
        return f"{self.resource(component)}-{settings.IMAGE_TAG}.tar"


@dataclass(frozen=True)
class Database:
    """One PostgreSQL workload: its pod, data, roles, passwords and replication.

    For name="notes": container notes-postgres, unit notes-postgres.kube,
    password secret notes-db-password, role notes_migrator, slot
    notes_standby and volume notes-postgres-data.
    """

    name: str
    replication_port: int = 5432

    @property
    def names(self) -> Names:
        """The naming rules for this database's name, for the rare other resource."""
        return Names(self.name)

    # The database pod.

    @property
    def container(self) -> str:
        return self.names.resource("postgres")

    @property
    def unit(self) -> str:
        return self.names.unit("postgres")

    @property
    def service(self) -> str:
        return self.names.service("postgres")

    @property
    def manifest(self) -> str:
        return self.names.manifest("postgres")

    @property
    def config_manifest(self) -> str:
        """The ConfigMap file; an app's pod reads its own ConfigMap from the same file."""
        return self.names.manifest("config")

    @property
    def kube_secret(self) -> str:
        return self.names.kube_secret("postgres")

    @property
    def image(self) -> str:
        return settings.POSTGRES_IMAGE

    @property
    def image_archive(self) -> str:
        return f"postgres-{settings.POSTGRES_VERSION}.tar"

    # Data, roles and passwords.

    def volume(self, purpose: str) -> str:
        """A volume of this database, such as todo-postgres-data."""
        return self.names.resource("postgres-" + purpose)

    def role(self, role: str) -> str:
        """A PostgreSQL role, such as todo_migrator."""
        return f"{self.name}_{role}"

    def secret(self, role: str) -> str:
        """The Podman secret holding a role's password, such as todo-migrator-password."""
        return self.names.resource(role) + "-password"

    # Replication.

    def replication_slot(self, rebuilt: bool = False) -> str:
        return self.role("rebuilt_standby" if rebuilt else "standby")

    def replication_passfile(self) -> str:
        return "." + self.names.resource("replication") + ".pgpass"
