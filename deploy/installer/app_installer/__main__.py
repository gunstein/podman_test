"""Entry point for python -m app_installer."""
from . import oplog
from .cli import main

raise SystemExit(oplog.run('app-installer', main))
