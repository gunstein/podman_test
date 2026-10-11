"""Each app's database roles, made by the platform (docs/PLATFORM-PLAN.md, section 5.5).

An app with database: true gets three roles in its own PostgreSQL:

- the bootstrap owner (named like the app, e.g. shop), which PostgreSQL's
  image creates with the database and which only the platform uses;
- <app>_migrator, which owns the public schema and every table in it and
  runs the app's migrations (its init container);
- <app>_app, the runtime role the backend logs in as: it may connect and use
  the schema, and nothing more until the app's own migrations grant it rights
  on the tables they create (GRANT SELECT, INSERT, ... TO <app>_app).

No other role may connect. setup() makes this true on every install, after
the database is healthy and before the app starts; running it again changes
nothing. It never grants or revokes table rights: those belong to the
migrations, so a migration's own grants are kept. The passwords come from the
roles' Podman secrets and go to psql on stdin, never in argv or a log.
"""
from . import secrets
from .commands import run

SCHEMA = "public"

# Every table, view and sequence in the schema, to be owned by the migrator; a
# sequence owned by a table column moves with its table, so it is left out.
OWN_OBJECTS = """
DO $own$ DECLARE object record; BEGIN
  FOR object IN
    SELECT c.relname, CASE c.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                                     WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
                                     ELSE 'TABLE' END AS kind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = {schema} AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
      AND NOT (c.relkind = 'S' AND EXISTS (
        SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))
  LOOP
    EXECUTE format('ALTER %s %I.%I OWNER TO %I', object.kind, {schema}, object.relname, {migrator});
  END LOOP;
END $own$;
"""


def literal(value):
    """value as an SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def identifier(value):
    """value as an SQL identifier."""
    return '"' + value.replace('"', '""') + '"'


def statements(database_name, migrator, runtime, migrator_password, runtime_password):
    """The SQL that makes the roles of one app's database as the module docstring says."""
    roles = (migrator, runtime)
    create = "".join(f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {literal(role)}) "
                     f"THEN CREATE ROLE {identifier(role)} LOGIN; END IF;\n" for role in roles)
    alter = "".join(f"ALTER ROLE {identifier(role)} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    f"NOREPLICATION NOINHERIT PASSWORD {literal(password)};\n"
                    for role, password in ((migrator, migrator_password), (runtime, runtime_password)))
    search_path = "".join(f"ALTER ROLE {identifier(role)} IN DATABASE {identifier(database_name)} "
                          f"SET search_path = {identifier(SCHEMA)};\n" for role in roles)
    return (f"DO $roles$ BEGIN\n{create}END $roles$;\n{alter}"
            f"REVOKE CONNECT ON DATABASE {identifier(database_name)} FROM PUBLIC;\n"
            f"GRANT CONNECT ON DATABASE {identifier(database_name)} TO {identifier(migrator)}, {identifier(runtime)};\n"
            f"ALTER SCHEMA {identifier(SCHEMA)} OWNER TO {identifier(migrator)};\n"
            + OWN_OBJECTS.format(schema=literal(SCHEMA), migrator=literal(migrator))
            + f"REVOKE ALL ON SCHEMA {identifier(SCHEMA)} FROM PUBLIC;\n"
            f"GRANT USAGE ON SCHEMA {identifier(SCHEMA)} TO {identifier(runtime)};\n"
            + search_path)


def setup(app):
    """Make app's database roles in its running PostgreSQL, as its bootstrap owner over the local socket."""
    database = app.database
    sql = statements(database.name, database.role("migrator"), database.role("app"),
                     secrets.read(database.secret("migrator")), secrets.read(database.secret("app")))
    run("podman", "exec", "--interactive", database.container, "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1",
        "--quiet", "--username", database.name, "--dbname", database.name,
        input=sql, secret_output=True, description=f"{app.name}: database roles")
