"""Render today's Kube YAML, Quadlet units and bundle.json, to pin them as a baseline.

The platform work (docs/PLATFORM-PLAN.md) changes how the installation is
described, not what it renders, except in the phases that say so. This file
renders everything the installer writes from the repository's templates, in
build mode and as an offline bundle, so tests/test_render_baseline.py can
compare it with the files kept in tests/fixtures/render-baseline.

After an intended change, write the new baseline and review its diff in Git:

    python3 tests/render_baseline.py --update
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/installer"))

from app_installer import apps, bundle, render  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/render-baseline"
ENVIRONMENTS = ("local", "prod")


def _values(environment):
    return ROOT / "deploy/environments" / environment / "values.yaml"


def rendered():
    """Every rendered file: {relative path: bytes}."""
    files = {}
    for environment in ENVIRONMENTS:
        # Build mode: the Kube YAML render.render writes, and the units an
        # install writes for a host that publishes only on 127.0.0.1.
        identity, port, log_level = render.read_values(_values(environment))
        manifests = render.files(ROOT, apps.registry(), render.hostnames(apps.registry()), identity, port, log_level)
        for name, content in manifests.items():
            files[f"build-{environment}/manifests/{name}"] = content
        for name, content in bundle.quadlets(ROOT, apps.registry(), port, "127.0.0.1").items():
            files[f"build-{environment}/quadlet/{name}"] = content
    # The offline bundle: generated/target and bundle.json, as build-bundle.sh makes them.
    with tempfile.TemporaryDirectory() as directory:
        bundle.build(ROOT, _values("prod"), directory)
        for path in sorted(Path(directory).rglob("*")):
            if path.is_file():
                files["bundle/" + path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def kept():
    """The baseline kept in tests/fixtures/render-baseline: {relative path: bytes}."""
    return {path.relative_to(FIXTURES).as_posix(): path.read_bytes()
            for path in sorted(FIXTURES.rglob("*")) if path.is_file()}


def update():
    """Replace the kept baseline with what the repository renders now."""
    for path in sorted(FIXTURES.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    for name, content in rendered().items():
        path = FIXTURES / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        sys.exit("Usage: python3 tests/render_baseline.py --update")
    update()
    print(f"Wrote {len(kept())} files to {FIXTURES.relative_to(ROOT)}")
