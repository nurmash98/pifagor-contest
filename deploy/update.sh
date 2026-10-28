#!/usr/bin/env bash
# sudo pifagor-update — подтягивает новый код из GitHub и перезапускает сайт (аналог автодеплоя Render).
# Если новых коммитов нет — ничего не делает. Запускается вручную или таймером pifagor-update.timer.
set -euo pipefail
APP_DIR=/opt/pifagor-contest
BRANCH="${BRANCH:-feature/tags-and-courses}"
as_app() { runuser -u pifagor -- "$@"; }

as_app git -C "$APP_DIR" fetch -q origin "$BRANCH"
OLD="$(as_app git -C "$APP_DIR" rev-parse HEAD)"
NEW="$(as_app git -C "$APP_DIR" rev-parse "origin/$BRANCH")"
[ "$OLD" != "$NEW" ] || { echo "Уже последняя версия ($OLD)"; exit 0; }

echo "Обновляю $OLD -> $NEW"
as_app git -C "$APP_DIR" reset --hard "origin/$BRANCH"
as_app "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/req.txt"
/usr/local/sbin/pifagor-backup                       # копия базы перед миграциями
/usr/local/sbin/pifagor-manage migrate --noinput
/usr/local/sbin/pifagor-manage collectstatic --noinput -v 0
systemctl restart pifagor
sleep 2
systemctl is-active --quiet pifagor && echo "Готово: сайт обновлён" || { echo "!! Сайт не запустился: journalctl -u pifagor -n 50"; exit 1; }
