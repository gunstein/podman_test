"""A real bundle's target files, filled in for one DR host, for the DR host tests.

The operations package carries the same bundle.json and generated/target as
the offline bundle (build-operations-package.sh); app_dr_host loads them with
target_render.load_on_host. Rendering needs Jinja2 and PyYAML, so the bundle is
built once in the test process, as the build host would.
"""
import atexit
import functools
import shutil
import tempfile
from pathlib import Path

from tests.platform_fixture import PLATFORM_FILE

ROOT = Path(__file__).resolve().parents[3]


@functools.cache
def bundle():
    """The directory of one bundle for every app, built from this repository."""
    from app_installer import bundle as builder
    directory = Path(tempfile.mkdtemp())
    atexit.register(shutil.rmtree, directory, True)
    with tempfile.TemporaryDirectory() as project:
        root = Path(project)
        for part in ('examples', 'deploy'):
            (root / part).symlink_to(ROOT / part, target_is_directory=True)
        (root / 'platform.yaml').write_text(PLATFORM_FILE.read_text().replace('../../examples/', 'examples/'))
        builder.build(root, 'prod', directory)
    return directory


def load(address='192.0.2.10', **hostnames):
    """The bundle's files filled in for a host at address; hostnames are TARGET_..._HOSTNAME overrides."""
    from app_installer import target_render
    return target_render.load(bundle(), {target_render.PUBLISH_ADDRESS: address, **hostnames},
                              environment={}, recorded={})
