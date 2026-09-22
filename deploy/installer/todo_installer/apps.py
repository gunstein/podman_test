"""Application registry: per-application identity for the shared installer."""
from dataclasses import dataclass


@dataclass(frozen=True)
class App:
    name: str
    chart: str
    hostname: str
    keycloak_client: str


APPS = (
    App(name="todo", chart="todo", hostname="todo.test", keycloak_client="todo-frontend"),
)
