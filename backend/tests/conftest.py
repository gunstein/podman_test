import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from backend.main import connect
from backend.migrate import applied_versions, migrate_down, migrate_up


def migration_versions() -> set[int]:
    with connect() as connection:
        return applied_versions(connection)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.exit("TEST_DATABASE_URL must be set")

    database_name = conninfo_to_dict(test_url).get("dbname", "")
    if not database_name.endswith("_test"):
        pytest.exit("Refusing to run: test database name must end with _test")

    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url

    while migration_versions():
        migrate_down()
    migrate_up()

    yield

    while migration_versions():
        migrate_down()

    if previous_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_url


@pytest.fixture(autouse=True)
def empty_todos():
    with connect() as connection:
        connection.execute("TRUNCATE TABLE todos RESTART IDENTITY")
