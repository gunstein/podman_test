"""Render workloads and Quadlets with the same tools used by deployment."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.TemporaryDirectory()
RUNTIME = Path(_tmp.name)

subprocess.run(
    [str(ROOT / "scripts/render-kube-runtime.sh"),
     str(ROOT / "helm/todo/values-prod.yaml"), str(RUNTIME)],
    check=True,
)


def render_units(destination, publish_address, postgres_address=""):
    destination.mkdir(parents=True, exist_ok=True)
    templates = {
        "todo-app": "application_kube_runtime",
        "todo-keycloak": "application_kube_runtime",
        "todo-postgres": "postgres_kube_runtime",
        "shared-proxy": "shared_proxy_runtime",
    }
    playbook = destination / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Render test Quadlets",
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {
            "todo_publish_address": publish_address,
            "todo_service_port": 8443,
            "todo_postgres_publish_address": postgres_address,
        },
        "tasks": [{
            "name": "Render " + unit,
            "ansible.builtin.template": {
                "src": str(ROOT / "ansible/roles" / role / "templates" / (unit + ".kube.j2")),
                "dest": str(destination / (unit + ".kube")),
                "mode": "0600",
            },
        } for unit, role in templates.items()],
    }]))
    subprocess.run(
        [os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook"),
         "-i", "localhost,", "-c", "local", str(playbook)],
        check=True, capture_output=True, text=True,
    )
    playbook.unlink()


render_units(RUNTIME, "192.0.2.10")
for name in ("README.md", "RESULTS.md"):
    shutil.copy(ROOT / "kube/runtime" / name, RUNTIME)
