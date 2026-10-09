"""Entry point for python -m app_dr_host."""
from app_installer import oplog

from .cli import main

raise SystemExit(oplog.run('app-dr-host', main))
