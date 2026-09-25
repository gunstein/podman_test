"""The installer's few actually-adjustable values, gathered in one place.

Everything else here (container, secret, unit and volume names) is a naming
rule derived from the App/Database registry in apps.py, not a setting: it
must stay a fixed contract other tools (Ansible, the DR/backup CLIs, the
quarantine helper) also derive independently, so it belongs there, not here.
"""
from pathlib import Path

# Image build tag; bump together with a rebuilt offline bundle.
IMAGE_TAG = "m12"

# Upstream PostgreSQL version, pinned like every other image tag above.
POSTGRES_VERSION = "17.11"
POSTGRES_IMAGE = f"docker.io/library/postgres:{POSTGRES_VERSION}"

# The shared proxy's fixed loopback bindings (deploy/quadlet/shared-proxy.kube.j2)
# and its external HTTPS port default; see workloads.install_shared_proxy for
# why --publish-address can never be a wildcard address here.
LOCAL_HTTP_PORT = 8080
HTTPS_PORT = 8443

# Default Quadlet directory for a single-host install; DR passes its own.
QUADLET_DIR = Path.home() / ".config/containers/systemd"

# Dev mode (direct `podman kube play`/`down`, no Quadlet units) records what it
# played here so `down` can find and remove it, independent of whatever
# --rendered-manifest-dir or --quadlet-dir a later `down` call happens to pass.
DEV_STATE_FILE = Path.home() / ".config/containers/app-installer-dev.json"
