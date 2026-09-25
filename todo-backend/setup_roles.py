"""Create the Todo database roles and give each only the rights it needs.

The installer runs this once per install, as a one-off container, logged in
as the bootstrap owner role (todo). It is safe to run again. Afterwards:

- todo_migrator owns the schema and every table, and runs migrations;
- todo_app can connect and read and write rows in the todos table, nothing more;
- no other role except the bootstrap owner can connect to the database.

Passwords come from mounted Podman secrets, never from the environment.
"""
import os
from pathlib import Path

import psycopg
from psycopg import sql


def read_secret(name: str) -> str:
    """Read a mounted secret file; raise if it is empty."""
    directory = Path(os.getenv("DATABASE_SECRETS_DIRECTORY", "/run/secrets"))
    value = (directory / name).read_text().strip()
    if not value:
        raise RuntimeError(f"Secret {name} is empty")
    return value


def ensure_login_role(connection, name: str, password: str) -> None:
    """Create the login role if needed, then set its password and remove every extra right."""
    exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)
    ).fetchone()
    if exists is None:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name))
        )
    connection.execute(
        sql.SQL(
            "ALTER ROLE {} PASSWORD {} NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOINHERIT"
        ).format(sql.Identifier(name), sql.Literal(password))
    )


def transfer_schema_objects(connection, schema: str, owner: str) -> None:
    """Make owner the owner of every table, view and sequence in schema.

    Sequences owned by a table column are skipped: they change owner with
    their table.
    """
    kinds = {
        "r": "TABLE",
        "p": "TABLE",
        "S": "SEQUENCE",
        "v": "VIEW",
        "m": "MATERIALIZED VIEW",
        "f": "FOREIGN TABLE",
    }
    rows = connection.execute(
        """
        SELECT c.relname, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relkind = ANY(%s)
          AND NOT (
              c.relkind = 'S'
              AND EXISTS (
                  SELECT 1
                  FROM pg_depend d
                  WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')
              )
          )
        """,
        (schema, list(kinds)),
    ).fetchall()
    for name, kind in rows:
        connection.execute(
            sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                sql.SQL(kinds[kind]),
                sql.Identifier(schema),
                sql.Identifier(name),
                sql.Identifier(owner),
            )
        )


def main() -> None:
    """Create the roles and apply the rights described in the module docstring."""
    database = os.getenv("DATABASE_NAME", "todo")
    with psycopg.connect(
        host=os.getenv("DATABASE_HOST", "todo-postgres"),
        port=os.getenv("DATABASE_PORT", "5432"),
        dbname=database,
        user=os.getenv("DATABASE_BOOTSTRAP_USER", "todo"),
        password=read_secret("todo-db-password"),
    ) as connection:
        ensure_login_role(
            connection, "todo_migrator", read_secret("todo-migrator-password")
        )
        ensure_login_role(connection, "todo_app", read_secret("todo-app-password"))

        connection.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database)
            )
        )
        for role in ("todo_migrator", "todo_app"):
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )

        connection.execute("ALTER SCHEMA public OWNER TO todo_migrator")
        transfer_schema_objects(connection, "public", "todo_migrator")
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA public TO todo_app")
        connection.execute(
            "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM todo_app"
        )
        connection.execute(
            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM todo_app"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE todo_migrator IN SCHEMA public "
            "REVOKE ALL ON TABLES FROM todo_app"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE todo_migrator IN SCHEMA public "
            "REVOKE ALL ON SEQUENCES FROM todo_app"
        )
        if connection.execute("SELECT to_regclass('public.todos')").fetchone()[0]:
            connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE todos TO todo_app"
            )
        if connection.execute(
            "SELECT to_regclass('public.todos_id_seq')"
        ).fetchone()[0]:
            connection.execute(
                "GRANT USAGE, SELECT ON SEQUENCE todos_id_seq TO todo_app"
            )

        for role, search_path in (
            ("todo_migrator", "public"),
            ("todo_app", "public"),
        ):
            connection.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path = {}").format(
                    sql.Identifier(role),
                    sql.Identifier(database),
                    sql.Identifier(search_path),
                )
            )

    print("Database roles and privileges are ready")


if __name__ == "__main__":
    main()
