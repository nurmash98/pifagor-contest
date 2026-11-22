#!/bin/bash
# Автообновление (launchd kz.pifagor.update, раз в минуту) — как автодеплой Render:
# новые коммиты в GitHub → зависимости → check → migrate → collectstatic → плавный перезапуск.
# При ошибке — откат на прошлую версию и уведомление. Принудительно: FORCE=1 bash deploy/macos/update.sh
set -uo pipefail
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$PROJECT/deploy/macos/env.sh"
mkdir -p "$PIFAGOR_DATA"; LOG="$PIFAGOR_DATA/update.log"; LOCK="$PIFAGOR_DATA/update.lock.d"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
notify() { osascript -e "display notification \"$1\" with title \"Pifagor Contest\"" >/dev/null 2>&1 || true; }
if ! mkdir "$LOCK" 2>/dev/null; then
  [ -n "$(find "$LOCK" -maxdepth 0 -mmin +15 2>/dev/null)" ] && rmdir "$LOCK" && mkdir "$LOCK" || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
cd "$PROJECT" || exit 1
BRANCH="${DEPLOY_BRANCH:-feature/tags-and-courses}"
git fetch -q origin "$BRANCH" 2>>"$LOG" || exit 0
OLD="$(git rev-parse HEAD)"; NEW="$(git rev-parse "origin/$BRANCH")"
[ "$OLD" = "$NEW" ] && [ -z "${FORCE:-}" ] && exit 0
[ "$(cat "$PIFAGOR_DATA/update.failed" 2>/dev/null)" = "$NEW" ] && [ -z "${FORCE:-}" ] && exit 0
log "=== обновление ${OLD:0:7} → ${NEW:0:7}"
if ! git merge --ff-only -q "origin/$BRANCH" 2>>"$LOG"; then
  log "ПРОПУСК: на сервере есть свои изменения в коде (git status в $PROJECT)"; echo "$NEW" > "$PIFAGOR_DATA/update.failed"
  notify "Обновление пропущено: на сервере изменён код"; exit 1
fi
load_env "$PIFAGOR_ENV"
reload() {
  if [ -f "$PIFAGOR_DATA/gunicorn.pid" ] && kill -0 "$(cat "$PIFAGOR_DATA/gunicorn.pid")" 2>/dev/null; then
    kill -HUP "$(cat "$PIFAGOR_DATA/gunicorn.pid")"
  else
    launchctl kickstart -k "gui/$(id -u)/kz.pifagor.web"
  fi
}
fail() {
  log "ОШИБКА на шаге «$1» — откат на ${OLD:0:7}"
  [ "$OLD" != "$NEW" ] && git reset -q --hard "$OLD"
  echo "$NEW" > "$PIFAGOR_DATA/update.failed"; reload; notify "Ошибка обновления («$1») — сайт остался на прошлой версии"; exit 1
}
if [ -n "${FORCE:-}" ] || git diff --name-only "$OLD" "$NEW" | grep -qx 'req.txt'; then
  uv pip install -q --python venv/bin/python -r req.txt >>"$LOG" 2>&1 || fail "зависимости"
fi
venv/bin/python manage.py check >>"$LOG" 2>&1 || fail "check"
venv/bin/python manage.py migrate --noinput >>"$LOG" 2>&1 || fail "migrate"
venv/bin/python manage.py collectstatic --noinput -v0 >>"$LOG" 2>&1 || fail "collectstatic"
reload
rm -f "$PIFAGOR_DATA/update.failed"
log "готово: ${NEW:0:7}"
