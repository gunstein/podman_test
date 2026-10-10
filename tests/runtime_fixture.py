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
     "prod", str(RUNTIME)],
    check=True,
)


def render_units(destination, publish_address, postgres_address=""):
    from app_installer.quadlet import render
    destination.mkdir(parents=True, exist_ok=True)
    for unit in ("todo-app", "notes-app", "keycloak", "todo-postgres", "notes-postgres",
                "keycloak-postgres", "shared-proxy"):
        (destination / (unit + ".kube")).write_bytes(render(ROOT, unit + ".kube", {
            "publish_address": publish_address,
            "service_port": 8443,
            "postgres_publish_address": postgres_address,
            "required_services": ["todo-app.service", "notes-app.service", "keycloak.service"],
        }))


render_units(RUNTIME, "192.0.2.10")
shutil.copy(ROOT / "deploy/runtime/README.md", RUNTIME)
shutil.copy(ROOT / "docs/history/RESULTS.md", RUNTIME)
