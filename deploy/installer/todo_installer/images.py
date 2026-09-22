"""Build or load the five images consumed by the rendered workload YAML."""
import json
from pathlib import Path

from .commands import exists, run


def prepare(project_root, deployment_mode, bundle_directory="", refresh_images=False):
    if deployment_mode not in ("build", "offline"):
        raise ValueError("deployment_mode must be build or offline")
    if deployment_mode == "offline" and (not bundle_directory or refresh_images):
        raise ValueError("Offline deployment requires bundle_directory and forbids refresh_images.")
    root = Path(project_root)
    changed = {}
    for name in ("backend", "frontend", "proxy", "keycloak", "postgres"):
        image = ("docker.io/library/postgres:17.11" if name == "postgres"
                 else f"localhost/todo-{name}:m12")
        present = exists("image", image)
        changed[name] = False
        if not present or refresh_images:
            if deployment_mode == "offline":
                archive = "postgres-17.11.tar" if name == "postgres" else f"todo-{name}-m12.tar"
                run("podman", "load", "--input", Path(bundle_directory) / "images" / archive)
            elif name == "postgres":
                run("podman", "pull", image)
            else:
                run("podman", "build", *(["--pull"] if refresh_images else []),
                    "--file", root / name / "Containerfile", "--tag", image, root)
            changed[name] = True
        if name == "proxy":
            inspection = json.loads(run("podman", "image", "inspect", image).stdout)
            if (inspection[0].get("Labels") or {}).get("io.todo.proxy") != "nginx":
                raise RuntimeError(
                    "The existing localhost/todo-proxy:m12 image does not identify nginx. "
                    "In build mode rerun with refresh_images=true; in offline "
                    "mode load the proxy archive from the new verified bundle.")
    return changed
