"""Typed topology primitives shared by the application registry.

Database is the postgres-shaped naming surface every "database" consumer
(workloads, secrets, replication) already limits itself to. It is extracted
here, rather than duplicated, so a database-only workload (one with no
frontend/backend/OAuth client of its own) can reuse it without inheriting
apps.App's unrelated Application-only fields.
"""
from dataclasses import dataclass

from . import settings


@dataclass(frozen=True)
class Database:
    name: str
    replication_port: int = 5432

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
            return settings.POSTGRES_IMAGE
        return f"localhost/{self.resource(component)}:{settings.IMAGE_TAG}"

    def image_archive(self, component: str) -> str:
        if component == "postgres":
            return f"postgres-{settings.POSTGRES_VERSION}.tar"
        return f"{self.resource(component)}-{settings.IMAGE_TAG}.tar"
