"""Entry point for python -m app_ops."""
import sys

from app_installer import oplog

from .cli import main

# app-ops runs on the controller: it also keeps what it printed, one file per run.
sys.exit(oplog.run('app-ops', main, keep=True))
