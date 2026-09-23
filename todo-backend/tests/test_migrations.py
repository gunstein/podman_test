import importlib
from pathlib import Path

_backend = Path(__file__).resolve().parents[1].name
connect = importlib.import_module(_backend + ".main").connect
_migrate = importlib.import_module(_backend + ".migrate")
applied_versions, load_migrations, migrate_up = (
    _migrate.applied_versions, _migrate.load_migrations, _migrate.migrate_up)


def test_migrations_have_up_and_down_files():
    migrations = load_migrations()
    assert migrations
    assert [migration.version for migration in migrations] == sorted(
        migration.version for migration in migrations
    )
    assert all(migration.up_path.is_file() for migration in migrations)
    assert all(migration.down_path.is_file() for migration in migrations)


def test_migrate_up_is_idempotent():
    migrate_up()
    migrate_up()
    with connect() as connection:
        assert applied_versions(connection) == {
            migration.version for migration in load_migrations()
        }
