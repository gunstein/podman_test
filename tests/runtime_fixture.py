"""Render workloads and Quadlets with the same tools used by deployment."""

import shutil
import sys
import tempfile
from pathlib import Path

from tests.platform_fixture import fixture_platform  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/installer"))
_tmp = tempfile.TemporaryDirectory()
RUNTIME = Path(_tmp.name)

from app_installer import platform_file, render  # noqa: E402

render.render(ROOT, platform_file.Environment(8443, 'info'), RUNTIME, fixture_platform())


def render_units(destination, publish_address, postgres_address=""):
    """Every unit of the fixture platform, as a bundle renders them; each database also on postgres_address."""
    from app_installer import bundle, quadlet, workloads
    destination.mkdir(parents=True, exist_ok=True)
    platform = fixture_platform()
    units = bundle.quadlets(ROOT, platform, 8443, publish_address)
    for database in platform.replicated_databases:
        units[database.unit] = quadlet.render(ROOT, "postgres.kube",
                                              workloads.postgres_variables(database, postgres_address))
    for name, content in units.items():
        (destination / name).write_bytes(content)


render_units(RUNTIME, "192.0.2.10")
shutil.copy(ROOT / "deploy/runtime/README.md", RUNTIME)
shutil.copy(ROOT / "docs/history/RESULTS.md", RUNTIME)
