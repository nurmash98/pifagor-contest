#!/bin/bash
# Ежедневная резервная копия базы Pifagor Contest (запускается из update.sh раз в сутки, ночью):
#  1) pg_dump на этот Мак: ~/pifagor-data/backups, хранятся 14 дней;
#  2) копия в облако — в Neon (бесплатно): база там полностью заменяется сегодняшней.
# Вручную: bash ~/srv/pifagor-contest/deploy/macos/backup.sh
set -uo pipefail
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$PROJECT/deploy/macos/env.sh"
load_env "$PIFAGOR_ENV"
for d in /Applications/Postgres.app/Contents/Versions/16/bin /Applications/Postgres.app/Contents/Versions/latest/bin; do
  [ -x "$d/pg_dump" ] && { export PATH="$d:$PATH"; break; }
done
B="$PIFAGOR_DATA/backups"; LOG="$PIFAGOR_DATA/backup.log"; LOCK="$PIFAGOR_DATA/backup.lock.d"
mkdir -p "$B"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
notify() { osascript -e "display notification \"$1\" with title \"Pifagor Contest\"" >/dev/null 2>&1 || true; }
mkdir "$LOCK" 2>/dev/null || { [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ] && rmdir "$LOCK" && mkdir "$LOCK" || exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

case "${DATABASE_URL:-}" in *neon.tech*|'') log "пропуск: сайт работает не с локальной базой"; exit 0 ;; esac
F="$B/pifagor-$(date +%F).dump"
if pg_dump -Fc --no-owner --no-privileges "$DATABASE_URL" -f "$F.tmp" 2>>"$LOG"; then
  mv "$F.tmp" "$F"; log "копия на Маке: $F ($(du -h "$F" | cut -f1))"
else
  rm -f "$F.tmp"; log "ОШИБКА pg_dump"; notify "Резервная копия базы не сделана (pg_dump)"; exit 1
fi
find "$B" -name 'pifagor-*.dump' -mtime +14 -delete

# облачная копия: NEON_BACKUP_URL, иначе строка «NEON_DATABASE_URL=…» из pifagor.env (оставлена при переезде)
NEON="${NEON_BACKUP_URL:-$(sed -n 's/^#.*NEON_DATABASE_URL=//p' "$PIFAGOR_ENV" | head -n1)}"
if [ -z "$NEON" ]; then log "облачная копия: адрес Neon не задан"; exit 0; fi
NEON="${NEON/-pooler./.}"   # прямое подключение, не через пулер
ERRS="$PIFAGOR_DATA/backup-neon-errors.txt"
pg_restore --clean --if-exists --no-owner --no-privileges -d "$NEON" "$F" 2>"$ERRS"
n=$(grep -c '^pg_restore: error' "$ERRS" || true)
if [ "$n" = "0" ]; then
  log "копия в Neon обновлена"; rm -f "$ERRS"
else
  log "копия в Neon: $n ошибок (см. $ERRS)"; notify "Копия базы в Neon с ошибками ($n)"
fi
