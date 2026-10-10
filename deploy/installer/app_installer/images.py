"""Build or load each app's images and, once, the images every app shares."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import apps, platform_file, settings
from .commands import exists, run


@dataclass(frozen=True)
class Image:
    """One container image: its reference, offline archive name, and how a build makes it.

    context is the build context relative to the project root, None for an
    image that is pulled; containerfile is relative to the context.
    """
    component: str
    reference: str
    archive: str
    context: str | None
    containerfile: str = "Containerfile"


def image_list(app: apps.App) -> tuple[Image, ...]:
    """The images one app builds, as its app.yaml declares them (apps.AppImage)."""
    return tuple(Image(image.name, app.image(image.name), app.image_archive(image.name), image.context,
                       image.containerfile) for image in app.images)


def shared_images() -> tuple[Image, ...]:
    """The images that are prepared once however many apps there are.

    PostgreSQL runs every database (each app's and Keycloak's), so it is one
    shared image like the nginx proxy and Keycloak, not one per app.
    """
    postgres = apps.KEYCLOAK_DATABASE  # every Database has the same image and archive
    return (Image("postgres", postgres.image, postgres.image_archive, None),
            Image("proxy", apps.PROXY_IMAGE, apps.PROXY_ARCHIVE, ".", "proxy/Containerfile"),
            Image("keycloak", apps.KEYCLOAK_IMAGE, apps.KEYCLOAK_ARCHIVE, ".", "keycloak/Containerfile"))


def _prepare(project_root, deployment_mode, bundle_directory, refresh_images, specifications):
    """Make each image present locally; return {component: True if it was built, pulled or loaded}.

    build mode builds from each Containerfile (PostgreSQL is pulled);
    offline mode only loads archives from the verified bundle and never
    reaches the network. An existing image is kept unless refresh_images.
    """
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
                archive = Path(bundle_directory) / "images" / image.archive
                if not archive.is_file():
                    raise FileNotFoundError(f"The bundle {bundle_directory} has no image archive images/"
                                            f"{image.archive}; use the complete verified bundle.")
                run("podman", "load", "--input", archive, timeout=settings.IMAGE_TIMEOUT)
            elif image.context is None:
                run("podman", "pull", image.reference, timeout=settings.IMAGE_TIMEOUT)
            else:
                context = root / image.context
                run("podman", "build", *(["--pull"] if refresh_images else []),
                    "--file", context / image.containerfile, "--tag", image.reference, context,
                    timeout=settings.IMAGE_TIMEOUT)
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
            *, app: apps.App, include_shared=True):
    """Prepare one app's images, plus the shared ones unless include_shared is False."""
    specifications = image_list(app)
    if include_shared:
        specifications += shared_images()
    return _prepare(project_root, deployment_mode, bundle_directory, refresh_images, specifications)


def prepare_shared(project_root, deployment_mode, bundle_directory="", refresh_images=False):
    """Prepare only the shared images: PostgreSQL, the proxy and Keycloak."""
    return _prepare(project_root, deployment_mode, bundle_directory, refresh_images, shared_images())


def prepare_offline_group(bundle_directory, platform):
    """Load every missing image of the platform from a verified bundle; nothing is built or pulled."""
    changed = any(prepare_shared(bundle_directory, 'offline', bundle_directory).values())
    for app in platform.apps:
        changed = any(prepare(bundle_directory, 'offline', bundle_directory,
                              app=app, include_shared=False).values()) or changed
    return changed


def build_and_export(project_root, destination, platform):
    """Build and export each distinct image of the platform once for offline delivery."""
    specifications = {image.reference: image for app in platform.apps for image in image_list(app)}
    specifications.update({image.reference: image for image in shared_images()})
    _prepare(project_root, 'build', '', True, specifications.values())
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for image in specifications.values():
        run('podman', 'save', '--format', 'oci-archive', '--output',
            destination / image.archive, image.reference, timeout=settings.IMAGE_TIMEOUT)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Build and export registered offline images')
    parser.add_argument('project_root', type=Path)
    parser.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    platform = platform_file.load(arguments.project_root / platform_file.FILE)[0]
    build_and_export(arguments.project_root, arguments.destination, platform)
