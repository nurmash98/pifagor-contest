#!/usr/bin/env bash
# sudo pifagor-backup — копия базы в /var/backups/pifagor (хранятся 14 дней; одна копия в неделю — 8 недель).
# Если задан BACKUP_RSYNC_TARGET (например user@другой-компьютер:/backups/pifagor/), копия уйдёт и туда.
set -euo pipefail
DIR=/var/backups/pifagor
mkdir -p "$DIR"; chmod 700 "$DIR"
F="$DIR/pifagor-$(date +%F-%H%M).dump"
runuser -u postgres -- pg_dump -Fc pifagor > "$F"
runuser -u postgres -- pg_restore -l "$F" >/dev/null          # файл читается — копия не битая
[ "$(date +%u)" != 7 ] || cp "$F" "$DIR/weekly-$(date +%F).dump"
find "$DIR" -name 'pifagor-*.dump' -mtime +14 -delete
find "$DIR" -name 'weekly-*.dump' -mtime +56 -delete
if [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then rsync -a --delete "$DIR/" "$BACKUP_RSYNC_TARGET"; fi
echo "Копия: $F ($(du -h "$F" | cut -f1))"
