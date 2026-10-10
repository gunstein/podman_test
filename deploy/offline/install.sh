#!/bin/sh
# Install an extracted offline bundle on this host: check the checksums and
# the host (preflight.sh), then run the installer in offline server mode.
set -eu

bundle_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

usage() {
  echo "Usage: sh install.sh [--publish-address HOST_IPV4] [--target-APP-hostname NAME ...]" >&2
  echo "  for example --target-identity-hostname auth.example.org --target-todo-hostname todo.example.org --target-notes-hostname notes.example.org" >&2
  exit 2
}

# Each --target-...-hostname pair is kept, in order, at the end of "$@" for the
# installer, which knows the apps and checks every value (target_render.py).
# Without one, a hostname comes from the environment, the host's record or the
# bundle's default.
publish_address=127.0.0.1
count=$#
while [ "$count" -gt 0 ]; do
  [ "$count" -ge 2 ] || usage
  case "$1" in
    --publish-address) publish_address=$2 ;;
    --target-*-hostname) set -- "$@" "$1" "$2" ;;
    *) usage ;;
  esac
  shift 2
  count=$((count - 2))
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

set -- --publish-address "$publish_address" "$@"

export PYTHONPATH="$bundle_directory/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m app_installer install --mode server --deployment-mode offline \
  --project-root "$bundle_directory" --bundle-dir "$bundle_directory" "$@"
