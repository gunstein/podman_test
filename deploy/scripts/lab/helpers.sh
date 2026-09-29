# shellcheck shell=bash
# The helpers the command lines in docs/ACCEPTANCE-AGENT.md C9 use.
#
# acceptance.py step runs one of those lines at a time: it sources this file
# and runs the line with RUN (the run folder), RUN_ID and A (acceptance.py for
# this run) set. Nobody sources it by hand.
: "${RUN:?RUN must name the run folder}"

# A product command: the start time, the exact command, its full output and
# its exit status in logs/<step>.log. It runs once: an existing log means the
# step already ran, and it refuses.
product() {
  local log="$RUN/logs/$1.log"
  shift
  if [ -e "$log" ]; then
    echo "STOP: $log exists, the step already ran; never run it again" >&2
    return 1
  fi
  {
    echo "# start $(date --iso-8601=seconds)"
    printf '# command:'
    printf ' %q' "$@"
    echo
  } > "$log"
  "$@" >> "$log" 2>&1
  echo "exit=$?" >> "$log"
  tail -n 4 "$log"
}

# A command on a VM, and an app-ops command on the controller VM.
vm() { product "$1" ssh -o BatchMode=yes gunstein@"$2" "$3"; }
ops() { vm "$1" "$2" "cd ~/todo-operations && PYTHONPATH=\$PWD/deploy/dr PYTHONDONTWRITEBYTECODE=1 python3 -m app_ops $3"; }

# The base backup that step 08-4 logged for one database (todo or notes),
# such as base-20260928T191946Z; empty if the log has none.
backup_name() {
  sed -n "s/^$1: Verified base backup: \(base-[0-9TZ]*\)\$/\1/p" "$RUN/logs/08-4-backup-create.log"
}

# The onboot value that step 01-3 (do rollback of VM 107) read from its clean
# snapshot; phase 9 sets it back.
recorded_onboot() {
  python3 -c 'import json, sys
print(next(e["values"]["onboot"] for e in map(json.loads, open(sys.argv[1]))
           if e["step"] == "01-3" and e["result"] == "PASS"))' "$RUN/record.jsonl"
}
