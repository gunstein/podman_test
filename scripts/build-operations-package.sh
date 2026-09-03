#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-operations.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/todo-operations"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/ansible/roles" \
  "$package_directory/quadlet" \
  "$package_directory/kube" \
  "$package_directory/scripts" \
  "$package_directory/offline" \
  "$package_directory/docs"
mkdir -p "$(dirname "$output")"

cp "$project_root/ansible.cfg" "$package_directory/"

cp "$project_root/ansible/README.md" \
  "$project_root/ansible/STANDBY-ARCHITECTURE.md" \
  "$project_root/ansible/STANDBY-BOOTSTRAP.md" \
  "$project_root/ansible/PROMOTION.md" \
  "$project_root/ansible/APPLICATION-FAILOVER.md" \
  "$project_root/ansible/APPLICATION-KUBE-MIGRATION.md" \
  "$project_root/ansible/POSTGRES-KUBE-MIGRATION.md" \
  "$project_root/ansible/BACKUP-PITR.md" \
  "$project_root/ansible/RESTORE-REDUNDANCY.md" \
  "$project_root/ansible/DR-AUTOMATION.md" \
  "$project_root/ansible/preflight-standby.yml" \
  "$project_root/ansible/bootstrap-standby.yml" \
  "$project_root/ansible/install-dr-tool.yml" \
  "$project_root/ansible/replication-status.yml" \
  "$project_root/ansible/deploy-promoted-application.yml" \
  "$project_root/ansible/configure-backup.yml" \
  "$project_root/ansible/preflight-standby-rebuild.yml" \
  "$project_root/ansible/rebuild-standby.yml" \
  "$project_root/ansible/cluster-status.yml" \
  "$project_root/ansible/migrate-application-to-kube.yml" \
  "$project_root/ansible/rollback-application-to-container-quadlets.yml" \
  "$project_root/ansible/migrate-postgres-primary-to-kube.yml" \
  "$project_root/ansible/rollback-postgres-primary-to-container-quadlet.yml" \
  "$project_root/ansible/inventory-initial.example.ini" \
  "$project_root/ansible/inventory-recovery.example.ini" \
  "$project_root/ansible/requirements.txt" \
  "$project_root/ansible/sync-standby-secrets.yml" \
  "$package_directory/ansible/"
for role in \
  standby_preflight \
  postgres_primary \
  postgres_standby \
  todo_dr \
  promoted_application \
  postgres_backup \
  postgres_redundancy_primary \
  postgres_reseed_standby \
  kube_application_migration \
  kube_application_rollback \
  kube_postgres_primary_migration \
  kube_postgres_primary_rollback
do
  cp -r "$project_root/ansible/roles/$role" \
    "$package_directory/ansible/roles/"
done
cp -r "$project_root/ansible/tasks" "$package_directory/ansible/"
cp "$project_root/quadlet/todo.network" \
  "$project_root/quadlet/todo-postgres-data.volume" \
  "$package_directory/quadlet/"
cp -r "$project_root/kube/runtime" "$package_directory/kube/"
cp "$project_root/scripts/todo_dr.py" \
  "$project_root/scripts/todo_dr_run.py" \
  "$project_root/scripts/todo_backup.py" "$package_directory/scripts/"
cp "$project_root/offline/FAPOLICYD.md" "$package_directory/offline/"
cp "$project_root/docs/SECRETS.md" \
  "$project_root/docs/TLS.md" \
  "$project_root/docs/SELINUX.md" \
  "$project_root/docs/WHAT-YOU-LEARN.md" \
  "$project_root/docs/LEARNING-GUIDE.md" \
  "$project_root/docs/LAB-ACCEPTANCE.md" \
  "$package_directory/docs/"

source_revision=unknown
source_state=unknown
if source_revision=$(git -C "$project_root" rev-parse --verify HEAD 2>/dev/null); then
  source_state=clean
  if test -n "$(git -C "$project_root" status --porcelain --untracked-files=normal)"; then
    source_state=dirty
  fi
fi
printf 'package=todo-operations\nsource_revision=%s\nsource_state=%s\n' \
  "$source_revision" "$source_state" > "$package_directory/VERSION"
tar -czf "$output" -C "$work_directory" "$(basename "$package_directory")"
output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
