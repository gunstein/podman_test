#!/usr/bin/env bash
# Mutation-test the replication checks that guard promotion and data deletion.
# Prints every surviving mutant as a diff. See docs/MUTATION-TESTING.md.
# Needs: python -m pip install mutmut==3.8.0 pytest jinja2 PyYAML
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../installer"
checks='reseed_check rebuild_primary_check require_promoted_group require_standby
require_primary status streaming_status archive_health require_stopped_service
require_quarantined_group require_reseed_confirmations identifier address'

patterns=()
for check in $checks; do
  patterns+=("app_installer.replication.x_${check}__mutmut_*")
done

rm -rf mutants
mutmut run "${patterns[@]}" > /dev/null
survivors=$(mutmut results | awk -F: '/survived/ {print $1}')
for mutant in $survivors; do
  mutmut show "$mutant"
done
printf 'Surviving mutants: %s\n' "$(printf '%s' "$survivors" | grep -c . || true)"
