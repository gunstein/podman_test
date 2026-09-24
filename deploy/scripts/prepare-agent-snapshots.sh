#!/usr/bin/env bash
# One-time lab preparation for docs/ACCEPTANCE-AGENT.md A3, through the Proxmox
# token: roll each VM back to its current clean snapshot, authorize this
# laptop's SSH key, add lab-only passwordless sudo, then take snapshot
# clean-agent. DESTROYS the current state of both VMs.
# Usage: prepare-agent-snapshots.sh [VMID:IP:CLEAN_SNAPSHOT ...]
set -euo pipefail

vm_user=${VM_USER:-gunstein}
new_snapshot='clean-agent'
scripts=$(cd "$(dirname "$0")" && pwd)
[[ $# -gt 0 ]] || set -- 107:192.168.0.102:clean-ol9-primary 108:192.168.0.108:clean-ol9-standby

pve() { python3 "$scripts/pve_lab.py" "$@"; }
vm_status() {
  pve get "/nodes/{node}/qemu/$1/status/current" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])'
}
has_snapshot() {
  pve get "/nodes/{node}/qemu/$1/snapshot" |
    python3 -c 'import json, sys; sys.exit(sys.argv[1] not in [s["name"] for s in json.load(sys.stdin)])' "$2"
}
wait_for_ssh() {
  for _ in $(seq 60); do
    timeout 5 bash -c "</dev/tcp/$1/22" 2>/dev/null && return 0
    sleep 5
  done
  echo "SSH on $1 did not come up" >&2
  return 1
}

echo "This rolls back and re-snapshots: $*"
read -rp "It destroys the current state of these VMs. Type yes to continue: " answer
[[ $answer == yes ]] || exit 1
ls "$HOME"/.ssh/id_*.pub >/dev/null 2>&1 || ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"

for item in "$@"; do
  IFS=: read -r vmid ip clean <<<"$item"
  echo "== VM $vmid ($ip)"
  if has_snapshot "$vmid" "$new_snapshot"; then
    echo "Snapshot $new_snapshot already exists on VM $vmid; delete it in Proxmox first." >&2
    exit 1
  fi
  pve task "/nodes/{node}/qemu/$vmid/snapshot/$clean/rollback" >/dev/null
  [[ $(vm_status "$vmid") == running ]] || pve task "/nodes/{node}/qemu/$vmid/status/start" >/dev/null
  wait_for_ssh "$ip"

  echo "Enter the password of $vm_user@$ip if asked:"
  ssh-copy-id "$vm_user@$ip"
  echo "Enter the sudo password of $vm_user on $ip if asked:"
  ssh -t "$vm_user@$ip" "echo '$vm_user ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/90-todo-acceptance >/dev/null && sudo chmod 0440 /etc/sudoers.d/90-todo-acceptance && sudo visudo -c -q"
  ssh -o BatchMode=yes "$vm_user@$ip" 'sudo -k; sudo -n true' || { echo "Passwordless sudo check failed on $ip" >&2; exit 1; }

  echo "Installing python3-jinja2 and python3-pyyaml (target install prerequisites)"
  ssh -o BatchMode=yes "$vm_user@$ip" 'sudo -n dnf install -y python3-jinja2 python3-pyyaml' >/dev/null
  ssh -o BatchMode=yes "$vm_user@$ip" "python3 -c 'import jinja2, yaml'" ||
    { echo "python3-jinja2/python3-pyyaml still missing on $ip" >&2; exit 1; }

  pve task "/nodes/{node}/qemu/$vmid/status/shutdown" >/dev/null
  pve task "/nodes/{node}/qemu/$vmid/snapshot" snapname="$new_snapshot" \
    description="Clean baseline plus laptop SSH key, lab-only passwordless sudo and python3-jinja2/pyyaml" >/dev/null
  pve task "/nodes/{node}/qemu/$vmid/status/start" >/dev/null
  wait_for_ssh "$ip"
  echo "VM $vmid ready: snapshot $new_snapshot taken, VM running."
done
echo "Done. Now run deploy/scripts/acceptance_preflight.py."
