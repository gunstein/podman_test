#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 user@target-host [expected-ed25519-fingerprint]" >&2
  echo "Installs this host's Ansible control key on the target, as documented" >&2
  echo "in ansible/STANDBY-ARCHITECTURE.md. Never accepts an unknown host key" >&2
  echo "without an independently verified fingerprint (SHA256:...)." >&2
  exit 1
fi

target=$1
expected_fingerprint=${2:-}
host=${target#*@}
key_file="$HOME/.ssh/id_rsa"

test -f "$key_file" || ssh-keygen -t rsa -b 3072 -N '' -C "todo-ansible-control" -f "$key_file"

if [[ -n "$expected_fingerprint" ]]; then
  if ssh-keygen -F "$host" >/dev/null 2>&1; then
    echo "Host $host is already known; skipping fingerprint verification."
  else
    scanned=$(ssh-keyscan -t ed25519 "$host" 2>/dev/null)
    if [[ -z "$scanned" ]]; then
      echo "Could not scan an ED25519 host key from $host." >&2
      exit 1
    fi
    actual_fingerprint=$(ssh-keygen -lf - <<<"$scanned" | awk '{print $2}')
    if [[ "$actual_fingerprint" != "$expected_fingerprint" ]]; then
      echo "Host key fingerprint mismatch for $host." >&2
      echo "Expected: $expected_fingerprint" >&2
      echo "Actual:   $actual_fingerprint" >&2
      echo "Refusing to trust this host. Verify the fingerprint through an" >&2
      echo "independently verified connection before retrying." >&2
      exit 1
    fi
    echo "$scanned" >> "$HOME/.ssh/known_hosts"
    echo "Verified and pinned host key fingerprint: $actual_fingerprint"
  fi
else
  echo "No expected fingerprint given; falling back to interactive host-key" >&2
  echo "acceptance. Prefer passing a fingerprint obtained through an" >&2
  echo "independently verified connection." >&2
fi

ssh-copy-id -i "${key_file}.pub" "$target"
ssh -o BatchMode=yes "$target" hostname
