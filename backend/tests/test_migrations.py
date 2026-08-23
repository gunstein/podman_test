from backend.migrate import load_migrations


def test_migrations_have_up_and_down_files():
    migrations = load_migrations()
    assert migrations
    assert [migration.version for migration in migrations] == sorted(
        migration.version for migration in migrations
    )
    assert all(migration.up_path.is_file() for migration in migrations)
    assert all(migration.down_path.is_file() for migration in migrations)
