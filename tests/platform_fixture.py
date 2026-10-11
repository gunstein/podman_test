"""A fixed platform for behaviour tests, independent of the checkout's app set."""
import os
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_FILE = ROOT / 'tests/fixtures/platform.yaml'


@lru_cache(maxsize=1)
def fixture_platform():
    """Todo, Notes and Help, with build paths relative to the repository root."""
    from app_installer import platform_file
    platform, _ = platform_file.load(PLATFORM_FILE)
    return replace(platform, apps=tuple(
        replace(app,
                pod_template=os.path.relpath(PLATFORM_FILE.parent / app.pod_template, ROOT),
                images=tuple(replace(image, context=os.path.relpath(
                    PLATFORM_FILE.parent / image.context, ROOT)) for image in app.images))
        for app in platform.apps))
