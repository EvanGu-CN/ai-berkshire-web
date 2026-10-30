#!/usr/bin/env bash
# Incremental releases for an existing installation; never provisions other services.
set -euo pipefail
umask 077
exec 9>"$VC_REMOTE_DIR/.deploy.lock"
flock -n 9 || { echo 'deployment_locked'; exit 1; }
# Also clean up code archives on an already-current release or preparation failure.
trap 'rm -f -- "$VC_ARCHIVE"' EXIT
old_release=$(readlink -f "$VC_REMOTE_DIR/current")
case "$old_release/" in "$VC_REMOTE_DIR/releases/"*) ;; *) echo 'invalid_current_release'; exit 1;; esac
test -d "$old_release/ai-berkshire-web/server"
test -f "$VC_REMOTE_ENV"
test -f "$VC_DATA_DIR/app.db"
test -d "$VC_REMOTE_DIR/shared/ai-berkshire/skills"
systemctl is-active --quiet "$VC_SERVICE"
test "$(sha256sum "$VC_ARCHIVE" | cut -d' ' -f1)" = "$VC_ARCHIVE_SHA256"

if [ -f "$old_release/REVISION" ] && [ "$(cat "$old_release/REVISION")" = "$VC_COMMIT" ]; then
  curl -fsS --max-time 10 "$VC_HEALTH_URL" >/dev/null
  echo "deployed_commit=$VC_COMMIT already_current=true"
  exit 0
fi

app_user=$(systemctl show -p User --value "$VC_SERVICE")
app_group=$(systemctl show -p Group --value "$VC_SERVICE")
test -n "$app_user"
app_group=${app_group:-$app_user}
echo 'deployment_phase=prepare'
release=$(mktemp -d "$VC_REMOTE_DIR/releases/$VC_COMMIT-XXXXXXXX")
chmod 755 "$release"
tar -xzf "$VC_ARCHIVE" -C "$release"
ln -s "$VC_REMOTE_DIR/shared/ai-berkshire" "$release/ai-berkshire"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install --quiet -r "$release/ai-berkshire-web/requirements.txt"
"$release/.venv/bin/python" -m compileall -q "$release/ai-berkshire-web/server"
printf '%s\n' "$VC_COMMIT" > "$release/REVISION"
chown -R "$app_user:$app_group" "$release"

stopped=0
switched=0
finish() {
  result=$?
  trap - EXIT
  if [ "$result" != 0 ]; then
    if [ "$switched" = 1 ]; then
      ln -sfn "$old_release" "$VC_REMOTE_DIR/current.rollback"
      mv -Tf "$VC_REMOTE_DIR/current.rollback" "$VC_REMOTE_DIR/current"
    fi
    if [ "$stopped" = 1 ]; then
      systemctl restart "$VC_SERVICE" || true
      echo 'previous_release_restarted=true'
    fi
  fi
  rm -f -- "$VC_ARCHIVE"
  exit "$result"
}
trap finish EXIT

# Refuse a planned restart while a user's research task is running.
python3 - "$VC_DATA_DIR/app.db" <<'PY'
import sqlite3, sys
with sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True) as conn:
    active = conn.execute("SELECT COUNT(*) FROM research_usage WHERE status='running'").fetchone()[0]
if active:
    sys.exit('active_research_tasks; retry deployment after completion')
PY

systemctl stop "$VC_SERVICE"
stopped=1
echo 'deployment_phase=backup'
install -d -m 700 "$VC_BACKUP_DIR"
backup=$(mktemp -d "$VC_BACKUP_DIR/$VC_COMMIT-XXXXXXXX")
tar -C "$VC_DATA_DIR" -czf "$backup/data.tar.gz" .
python3 "$release/ops/continuity.py" capture "$VC_DATA_DIR/app.db" "$backup/continuity.json"
echo "backup_dir=$backup"
echo 'deployment_phase=activate'
ln -sfn "$release" "$VC_REMOTE_DIR/current.new"
mv -Tf "$VC_REMOTE_DIR/current.new" "$VC_REMOTE_DIR/current"
switched=1
systemctl start "$VC_SERVICE"
echo 'deployment_phase=health'
ready=0
for attempt in $(seq 1 30); do
  if systemctl is-active --quiet "$VC_SERVICE" && curl -fsS --max-time 3 "$VC_HEALTH_URL" >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
test "$ready" = 1
echo 'deployment_phase=continuity'
python3 "$release/ops/continuity.py" verify "$VC_DATA_DIR/app.db" "$backup/continuity.json"
echo "deployed_commit=$VC_COMMIT"
echo 'local_health=passed'
