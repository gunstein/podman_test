import os
from pathlib import Path

import psycopg
from psycopg import sql


def read_secret(name: str) -> str:
    directory = Path(os.getenv("DATABASE_SECRETS_DIRECTORY", "/run/secrets"))
    value = (directory / name).read_text().strip()
    if not value:
        raise RuntimeError(f"Secret {name} is empty")
    return value


def ensure_login_role(connection, name: str, password: str) -> None:
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
    database = os.getenv("DATABASE_NAME", "notes")
    with psycopg.connect(
        host=os.getenv("DATABASE_HOST", "notes-postgres"),
        port=os.getenv("DATABASE_PORT", "5432"),
        dbname=database,
        user=os.getenv("DATABASE_BOOTSTRAP_USER", "notes"),
        password=read_secret("notes-db-password"),
    ) as connection:
        ensure_login_role(
            connection, "notes_migrator", read_secret("notes-migrator-password")
        )
        ensure_login_role(connection, "notes_app", read_secret("notes-app-password"))
        connection.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database)
            )
        )
        for role in ("notes_migrator", "notes_app"):
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )

        connection.execute("ALTER SCHEMA public OWNER TO notes_migrator")
        transfer_schema_objects(connection, "public", "notes_migrator")
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA public TO notes_app")
        connection.execute(
            "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM notes_app"
        )
        connection.execute(
            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM notes_app"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE notes_migrator IN SCHEMA public "
            "REVOKE ALL ON TABLES FROM notes_app"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE notes_migrator IN SCHEMA public "
            "REVOKE ALL ON SEQUENCES FROM notes_app"
        )
        if connection.execute("SELECT to_regclass('public.notes')").fetchone()[0]:
            connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE notes TO notes_app"
            )
        if connection.execute(
            "SELECT to_regclass('public.notes_id_seq')"
        ).fetchone()[0]:
            connection.execute(
                "GRANT USAGE, SELECT ON SEQUENCE notes_id_seq TO notes_app"
            )

        for role, search_path in (
            ("notes_migrator", "public"),
            ("notes_app", "public"),
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
