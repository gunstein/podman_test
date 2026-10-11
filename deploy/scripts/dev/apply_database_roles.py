"""CI: make an app's database roles with the platform's own SQL (app_installer.database_roles).

The backend job runs it against its PostgreSQL service, where podman exec is not
the way in, as

  PYTHONPATH=deploy/installer python deploy/scripts/dev/apply_database_roles.py \\
      --app shop --database shop_test --port 5432

logged in as the bootstrap owner (named like the app). The passwords come from
DATABASE_SECRETS_DIRECTORY/<app>-db-password, -migrator-password and
-app-password, as the job writes them. It lives in a .py file, not in the
workflow, so ruff and pyright check its calls.
"""
import argparse
import os
from pathlib import Path

import psycopg  # pyright: ignore[reportMissingImports]  (the backend job has it; the type-check job does not)
from app_installer import database_roles


def main():
    parser = argparse.ArgumentParser(description="Make an app's database roles with the platform's SQL")
    parser.add_argument('--app', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default='5432')
    arguments = parser.parse_args()
    directory = Path(os.environ['DATABASE_SECRETS_DIRECTORY'])

    def password(role):
        return (directory / f'{arguments.app}-{role}-password').read_text().strip()

    sql = database_roles.statements(arguments.database, f'{arguments.app}_migrator', f'{arguments.app}_app',
                                    password('migrator'), password('app'))
    with psycopg.connect(host=arguments.host, port=arguments.port, dbname=arguments.database,
                         user=arguments.app, password=password('db'), autocommit=True) as connection:
        connection.execute(sql)
    print(f'{arguments.app}: database roles made in {arguments.database}')


if __name__ == '__main__':
    main()
