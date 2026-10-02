#!/bin/sh
set -eu

bundle_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

usage() {
  echo "Usage: sh install.sh [--publish-address HOST_IPV4] [--target-external-hostname NAME]" >&2
  exit 2
}

# The installer checks the public hostname (app_installer/target_render.py);
# without the option it comes from TARGET_EXTERNAL_HOSTNAME or the bundle's default.
publish_address=127.0.0.1
external_hostname=
while [ "$#" -gt 0 ]; do
  [ "$#" -ge 2 ] || usage
  case "$1" in
    --publish-address) publish_address=$2 ;;
    --target-external-hostname) external_hostname=$2 ;;
    *) usage ;;
  esac
  shift 2
done

publish_address=$(python3 - "$publish_address" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.IPv4Address(sys.argv[1])
except ipaddress.AddressValueError:
    sys.exit("Publish address must be a host IPv4 address")
if address.is_unspecified or address.is_multicast or address.is_reserved:
    sys.exit("Publish address must identify a host, not a wildcard or reserved address")
print(address)
PY
)

cd "$bundle_directory"
sha256sum --check SHA256SUMS
sh "$bundle_directory/preflight.sh"

set -- --publish-address "$publish_address"
if [ -n "$external_hostname" ]; then
  set -- "$@" --target-external-hostname "$external_hostname"
fi

export PYTHONPATH="$bundle_directory/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m app_installer install --mode server --deployment-mode offline \
  --project-root "$bundle_directory" --bundle-dir "$bundle_directory" "$@"
