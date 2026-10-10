"""A real offline bundle's target files, rendered from this repository, for the installer tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def build(directory, applications):
    """Write generated/target and bundle.json for these apps into directory, as build-bundle.sh does.

    Rendering needs Jinja2 and PyYAML, so this runs in the test process
    (the build host); the install under test may run without them.
    """
    from app_installer import bundle
    return bundle.build(ROOT, 'prod', directory,
                        [app.name for app in applications])
