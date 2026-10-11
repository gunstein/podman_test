"""A real offline bundle's target files, rendered from this repository, for the installer tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def build(directory, applications):
    """Write generated/target, bundle.json and images/ for these apps into directory, as build-bundle.sh does.

    Rendering needs Jinja2 and PyYAML, so this runs in the test process
    (the build host); the install under test may run without them. Each
    image archive is the fake host's (fake_host.write_archive), and
    images/image-ids.json gives each image its fake ID.
    """
    from app_installer import apps, bundle
    from fake_host import write_images
    data = bundle.build(ROOT, 'prod', directory, [app.name for app in applications])
    write_images(directory, apps.Platform.from_json(data['platform']))
    return data
