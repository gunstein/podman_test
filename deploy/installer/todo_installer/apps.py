"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass


@dataclass(frozen=True)
class App:
    name: str
    chart: str
    hostname: str
    keycloak_client: str


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
    App(name="notes", chart="notes", hostname="notes.test", keycloak_client="notes-frontend"),
)

# The existing Todo database hosts the shared realm; preserve its stored credentials.
IDENTITY_DATABASE_APP = APPS[0]
NETWORK = "app-network"
KEYCLOAK_IMAGE = "localhost/keycloak:m12"
KEYCLOAK_ARCHIVE = "keycloak-m12.tar"
PROXY_IMAGE = IDENTITY_DATABASE_APP.image("proxy")
PROXY_ARCHIVE = IDENTITY_DATABASE_APP.image_archive("proxy")
