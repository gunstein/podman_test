#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 user@serving-host" >&2
  echo "Fetches, verifies and trusts the current serving host's public demo CA," >&2
  echo "as documented in docs/ACCEPTANCE.md (client trust and browser verification)." >&2
  exit 1
fi

ssh_target=$1
temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT

ssh -o StrictHostKeyChecking=yes "$ssh_target" \
  'podman exec nginx cat /var/lib/todo-tls/ca.crt' > "$temporary"

local_fingerprint=$(openssl x509 -in "$temporary" -noout -fingerprint -sha256)
remote_fingerprint=$(ssh -o BatchMode=yes "$ssh_target" \
  'podman exec nginx openssl x509 -in /var/lib/todo-tls/ca.crt -noout -fingerprint -sha256')

if [[ "$local_fingerprint" != "$remote_fingerprint" ]]; then
  echo "Fingerprint mismatch: SSH-retrieved certificate does not match the" >&2
  echo "serving host's own report. Refusing to trust it." >&2
  exit 1
fi
echo "Verified fingerprint: $local_fingerprint"

sudo cp "$temporary" /usr/local/share/ca-certificates/todo-nginx-root.crt
sudo update-ca-certificates

nssdb=$HOME/.pki/nssdb
[[ -d $nssdb ]] || nssdb=$HOME/.local/share/pki/nssdb
if [[ -d $nssdb ]]; then
  short=$(echo "$local_fingerprint" | sed 's/.*=//; s/://g' | tr 'A-F' 'a-f' | cut -c1-8)
  nickname="todo-lab-ca-$short"
  certutil -A -d "sql:$nssdb" -n "$nickname" -t 'C,,' -i "$temporary"
  echo "Trusted in system store and Chromium NSS database ($nssdb) as $nickname."
else
  echo "No NSS database found at ~/.pki/nssdb or ~/.local/share/pki/nssdb;" >&2
  echo "trusted in the system store only. Add Chromium trust manually if needed." >&2
fi
