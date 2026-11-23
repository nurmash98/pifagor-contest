#!/bin/bash
# Перенос базы Pifagor Contest: Neon (Франкфурт) → локальный PostgreSQL на этом Маке
# (тот же Postgres.app-сервер, что у infosabaq, порт 5432). Запуск один раз:
#   bash ~/srv/pifagor-contest/deploy/macos/move-db-to-mac.sh
# Сайт на время переноса остановлен (5–15 минут). Если что-то пойдёт не так — сайт
# запускается обратно со старой базой Neon, ничего не теряется.
set -euo pipefail
cd "$(dirname "$0")/../.."
source deploy/macos/env.sh
PGB=/Applications/Postgres.app/Contents/Versions/16/bin
[ -x "$PGB/psql" ] || PGB=/Applications/Postgres.app/Contents/Versions/latest/bin
export PATH="$PGB:$PATH"
UIDN=$(id -u)
AGENTS="$HOME/Library/LaunchAgents"
DUMP="$PIFAGOR_DATA/neon-dump.jsonl"
PY=venv/bin/python
step() { echo; echo "== $*"; }

load_env "$PIFAGOR_ENV"
NEON_URL="${DATABASE_URL:-}"
case "$NEON_URL" in *neon.tech*) ;; *) echo "В pifagor.env DATABASE_URL уже не Neon — перенос не нужен."; exit 0 ;; esac

step "1/7 Локальный PostgreSQL"
psql -h /tmp -d postgres -tAc "SELECT version()" >/dev/null || { echo "!! Локальный PostgreSQL (infosabaq) не отвечает"; exit 1; }
PW=$(openssl rand -hex 16)  # (tr | head падал из-за pipefail)
psql -h /tmp -d postgres -v ON_ERROR_STOP=1 -qc "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='pifagor') THEN CREATE ROLE pifagor LOGIN PASSWORD '$PW'; ELSE ALTER ROLE pifagor PASSWORD '$PW'; END IF; END \$\$;"
psql -h /tmp -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='pifagor'" | grep -q 1 || createdb -h /tmp -O pifagor -E UTF8 pifagor
LOCAL_URL="postgresql://pifagor:$PW@127.0.0.1:5432/pifagor?sslmode=disable"
echo "ок"

restart_site() {
  for s in web update; do
    launchctl bootout "gui/$UIDN/kz.pifagor.$s" 2>/dev/null || true
    launchctl bootstrap "gui/$UIDN" "$AGENTS/kz.pifagor.$s.plist" 2>/dev/null || true
  done
}
on_fail() { echo; echo "!! Перенос не удался — запускаю сайт обратно со старой базой Neon."; restart_site; }
trap on_fail ERR

step "2/7 Останавливаю сайт"
launchctl bootout "gui/$UIDN/kz.pifagor.update" 2>/dev/null || true
launchctl bootout "gui/$UIDN/kz.pifagor.web" 2>/dev/null || true
sleep 2
echo "ок"

step "3/7 Скачиваю данные из Neon (несколько минут)"
# прямой адрес Neon, без пулера: через пулер (PgBouncer) серверные курсоры dumpdata не работают
NEON_DIRECT="${NEON_URL/-pooler./.}"
DATABASE_URL="$NEON_DIRECT" $PY manage.py dumpdata --format jsonl --natural-foreign \
  -e contenttypes -e auth.permission -e sessions -e admin.logentry -o "$DUMP"
ls -lh "$DUMP" | awk '{print "файл: " $5}'

step "4/7 Создаю таблицы на Маке"
export DATABASE_URL="$LOCAL_URL"
$PY manage.py migrate --noinput -v0
$PY manage.py flush --noinput -v0

step "5/7 Загружаю данные (несколько минут)"
$PY manage.py loaddata "$DUMP"

step "6/7 Сверяю количество записей"
COUNT='from django.apps import apps
skip = {"contenttypes", "sessions", "admin"}
for m in sorted(apps.get_models(include_auto_created=True), key=lambda m: m._meta.label):
    if m._meta.app_label in skip or m._meta.label == "auth.Permission" or not m._meta.managed:
        continue
    print(m._meta.label, m._base_manager.count())'
DATABASE_URL="$NEON_DIRECT" $PY manage.py shell -c "$COUNT" > "$PIFAGOR_DATA/count-neon.txt"
$PY manage.py shell -c "$COUNT" > "$PIFAGOR_DATA/count-local.txt"
if ! diff "$PIFAGOR_DATA/count-neon.txt" "$PIFAGOR_DATA/count-local.txt"; then
  echo "!! Количество записей не совпадает (слева Neon, справа Мак)"; false
fi
echo "всё совпадает: $(awk '{s+=$2} END {print s}' "$PIFAGOR_DATA/count-local.txt") записей"

step "7/7 Переключаю сайт на базу Мака"
cp "$PIFAGOR_ENV" "$PIFAGOR_ENV.before-local-db"
TMP=$(mktemp)
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    DATABASE_URL=*) echo "# Старая база (Neon), для отката: NEON_$line"; echo "DATABASE_URL=$LOCAL_URL" ;;
    *) echo "$line" ;;
  esac
done < "$PIFAGOR_ENV" > "$TMP"
cat "$TMP" > "$PIFAGOR_ENV"; rm -f "$TMP"; chmod 600 "$PIFAGOR_ENV"
trap - ERR
restart_site
for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: pifagor.infosabaq.kz' http://127.0.0.1:8090/login/ || true)
  [ "$code" = "200" ] && break; sleep 2
done
rm -f "$DUMP"
if [ "$code" = "200" ]; then
  echo; echo "ГОТОВО. Сайт работает с базой на этом Маке."
  echo "Резервные копии базы теперь делайте с Мака (Render и Neon больше не нужны)."
else
  echo "!! Сайт не ответил (HTTP $code). Откат: cp $PIFAGOR_ENV.before-local-db $PIFAGOR_ENV и снова запустить сайт."
fi
