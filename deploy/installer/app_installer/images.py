"""Build or load application images and the singular shared infrastructure images."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import apps
from .commands import exists, run


@dataclass(frozen=True)
class Image:
    component: str
    reference: str
    archive: str
    source: str | None


def image_list(app: apps.App) -> tuple[Image, ...]:
    return tuple(Image(component, app.image(component), app.image_archive(component),
                       None if component == "postgres" else app.resource(component))
                 for component in ("backend", "frontend", "postgres"))


def shared_images() -> tuple[Image, ...]:
    return (Image("proxy", apps.PROXY_IMAGE, apps.PROXY_ARCHIVE, "proxy"),
            Image("keycloak", apps.KEYCLOAK_IMAGE, apps.KEYCLOAK_ARCHIVE, "keycloak"))


def _prepare(project_root, deployment_mode, bundle_directory, refresh_images, specifications):
    if deployment_mode not in ("build", "offline"):
        raise ValueError("deployment_mode must be build or offline")
    if deployment_mode == "offline" and (not bundle_directory or refresh_images):
        raise ValueError("Offline deployment requires bundle_directory and forbids refresh_images.")
    root = Path(project_root)
    changed = {}
    for image in specifications:
        present = exists("image", image.reference)
        changed[image.component] = False
        if not present or refresh_images:
            if deployment_mode == "offline":
                run("podman", "load", "--input", Path(bundle_directory) / "images" / image.archive)
            elif image.source is None:
                run("podman", "pull", image.reference)
            else:
                run("podman", "build", *(["--pull"] if refresh_images else []),
                    "--file", root / image.source / "Containerfile", "--tag", image.reference, root)
            changed[image.component] = True
        if image.component == "proxy":
            inspection = json.loads(run("podman", "image", "inspect", image.reference).stdout)
            if (inspection[0].get("Labels") or {}).get("io.todo.proxy") != "nginx":
                raise RuntimeError(
                    f"The existing {image.reference} image does not identify nginx. "
                    "In build mode rerun with refresh_images=true; in offline "
                    "mode load the proxy archive from the new verified bundle.")
    return changed


def prepare(project_root, deployment_mode, bundle_directory="", refresh_images=False,
            app: apps.App = apps.APPS[0], include_shared=True):
    specifications = image_list(app)
    if include_shared:
        specifications = specifications[:2] + shared_images() + specifications[2:]
    return _prepare(project_root, deployment_mode, bundle_directory, refresh_images, specifications)


def prepare_shared(project_root, deployment_mode, bundle_directory="", refresh_images=False):
    return _prepare(project_root, deployment_mode, bundle_directory, refresh_images, shared_images())


def build_and_export(project_root, destination):
    """Build and export each distinct registry image once for offline delivery."""
    specifications = {image.reference: image for app in apps.APPS for image in image_list(app)}
    specifications.update({image.reference: image for image in shared_images()})
    _prepare(project_root, 'build', '', True, specifications.values())
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for image in specifications.values():
        run('podman', 'save', '--format', 'oci-archive', '--output',
            destination / image.archive, image.reference)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Build and export registered offline images')
    parser.add_argument('project_root', type=Path)
    parser.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    build_and_export(arguments.project_root, arguments.destination)
