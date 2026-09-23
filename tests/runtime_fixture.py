"""Render workloads and Quadlets with the same tools used by deployment."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/installer"))
_tmp = tempfile.TemporaryDirectory()
RUNTIME = Path(_tmp.name)

subprocess.run(
    [str(ROOT / "deploy/scripts/render-kube-runtime.sh"),
     str(ROOT / "deploy/environments/prod/values.yaml"), str(RUNTIME)],
    check=True,
)


def render_units(destination, publish_address, postgres_address=""):
    from todo_installer.quadlet import render
    destination.mkdir(parents=True, exist_ok=True)
    for unit in ("todo-app", "notes-app", "keycloak", "todo-postgres", "notes-postgres",
                "keycloak-postgres", "shared-proxy"):
        (destination / (unit + ".kube")).write_bytes(render(ROOT, unit + ".kube", {
            "todo_publish_address": publish_address,
            "todo_service_port": 8443,
            "todo_postgres_publish_address": postgres_address,
            "app_services": ["todo-app.service", "notes-app.service"],
        }))


render_units(RUNTIME, "192.0.2.10")
for name in ("README.md", "RESULTS.md"):
    shutil.copy(ROOT / "deploy/runtime" / name, RUNTIME)
