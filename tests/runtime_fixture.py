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
    """Every unit of this checkout's platform, as a bundle renders them; each database also on postgres_address."""
    from app_installer import bundle, platform_file, quadlet, workloads
    destination.mkdir(parents=True, exist_ok=True)
    platform = platform_file.checkout()
    units = bundle.quadlets(ROOT, platform, 8443, publish_address)
    for database in platform.replicated_databases:
        units[database.unit] = quadlet.render(ROOT, "postgres.kube",
                                              workloads.postgres_variables(database, postgres_address))
    for name, content in units.items():
        (destination / name).write_bytes(content)


render_units(RUNTIME, "192.0.2.10")
shutil.copy(ROOT / "deploy/runtime/README.md", RUNTIME)
shutil.copy(ROOT / "docs/history/RESULTS.md", RUNTIME)
