"""Fixed values and paths the installer and the DR tools share, in one place.

Container, secret, unit and volume names are not here: each follows a
naming rule from the App/Database registry (apps.py, stack.py). They are a
fixed contract that other tools (app-ops, the DR/backup CLIs, the
quarantine helper) rely on, so they belong with the rules, not here.
"""
from pathlib import Path

# Image build tag; bump together with a rebuilt offline bundle. It also names
# the bundle, platform-offline-<tag>: build-bundle.sh reads it from here, and the
# guides that name the bundle must follow (tests/test_fixed_layout.py).
IMAGE_TAG = "m12"

# Upstream PostgreSQL version, pinned like every other image the stack runs.
POSTGRES_VERSION = "17.11"
POSTGRES_IMAGE = f"docker.io/library/postgres:{POSTGRES_VERSION}"

# The shared proxy's fixed loopback bindings (deploy/quadlet/shared-proxy.kube.j2)
# and its external HTTPS port default; see workloads.install_shared_proxy for
# why --publish-address can never be a wildcard address here.
LOCAL_HTTP_PORT = 8080
HTTPS_PORT = 8443

# Default Quadlet directory for a single-host install; DR passes its own.
QUADLET_DIR = Path.home() / ".config/containers/systemd"
# User units that are not Quadlets, such as the nightly backup timer.
SYSTEMD_USER_DIR = Path.home() / ".config/systemd/user"

# The directory next to the Quadlet units that holds the rendered Kube YAML
# the .kube units point at: QUADLET_DIR / KUBE_RUNTIME.
KUBE_RUNTIME = "platform-kube-runtime"

# Where app-ops installs the DR tools (app_dr.py, app_backup.py, the
# quarantine helper) and, under fapolicyd, the packages they import. This is a
# fixed layout, not a setting: the tools must find the packages before they
# can read this file, so app_dr.py and app_backup.py look in the lib next to
# their own bin, and app-quarantine.sh names /opt/platform/lib itself
# (tests/test_fixed_layout.py checks both). The installer also uses these
# paths, and DR_CONFIG below, to recognise and refuse a DR host.
TOOLS_BIN = Path("/opt/platform/bin")
TOOLS_LIB = Path("/opt/platform/lib")

# The DR settings and the promotion record, relative to the service user's
# home directory: ~/.config/platform/promotion.json on a host.
DR_CONFIG = ".config/platform"
PROMOTION_RECORD = "promotion.json"
# The public hostnames this host was installed with (target_render); install.sh
# and the DR tools write it, and every later step on the host reads it.
TARGET_RECORD = "target-values.json"
# The platform (its apps) this host was installed with (target_render.record_platform).
PLATFORM_RECORD = "platform.json"

# The recovery point objective app-ops writes into the DR settings. It is
# informational: app_dr.py status prints it, nothing enforces it.
RPO_TARGET_SECONDS = 30

# Dev mode (direct `podman kube play`/`down`, no Quadlet units) records what it
# played here so `down` can find and remove it, independent of whatever
# --rendered-manifest-dir or --quadlet-dir a later `down` call happens to pass.
# Where nginx's TLS files live: "secret", Podman secrets (app_installer/tls_secrets.py),
# or "volume", the TLS volume platform-nginx-data (app_installer/tls.py). To go back
# to the volume, set "volume" and follow the steps at the top of
# deploy/manifests/shared-proxy.yaml.j2 (docs/TLS.md, "Switching back").
NGINX_TLS_STORAGE = "secret"
DEV_STATE_FILE = Path.home() / ".config/containers/app-installer-dev.json"

# Time limits in seconds, so a command that hangs stops the step with an error
# instead of waiting forever. COMMAND_TIMEOUT is the default for any command;
# the others are for steps that are slow by nature.
COMMAND_TIMEOUT = 600
HEALTH_TIMEOUT = 300
IMAGE_TIMEOUT = 1800
# Copying one database (pg_basebackup, a base backup or a restore copy); over
# a slow link between sites this can take hours.
DATA_COPY_TIMEOUT = 4 * 3600
