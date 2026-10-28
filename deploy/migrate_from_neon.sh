#!/usr/bin/env bash
# Перенос базы из Neon на этот сервер. Можно повторять сколько угодно раз: каждый запуск
# ПОЛНОСТЬЮ заменяет локальную базу свежей копией из Neon (первый раз — репетиция, второй — боевой).
#
#   NEON_URL='postgresql://пользователь:пароль@хост/база?sslmode=require' bash migrate_from_neon.sh
#
# Строку подключения возьмите в Neon: Dashboard -> Connect -> Connection string (с паролем).
# Не отправляйте её никому и не сохраняйте в файлах: она нужна только для этой команды.
set -euo pipefail
: "${NEON_URL:?Задайте NEON_URL='postgresql://...' (см. комментарий в начале файла)}"
ENV_FILE=/etc/pifagor/pifagor.env
sudo test -f "$ENV_FILE" || { echo "Сначала установите сайт: bash deploy.sh"; exit 1; }

say() { printf '\n==> %s\n' "$*"; }
q() { psql "$NEON_URL" -tAc "$1"; }

say "Версии PostgreSQL"
SRV_NUM="$(q 'show server_version_num')"; SRV_MAJOR=$((SRV_NUM / 10000))
LOC_MAJOR="$(sudo -u postgres psql -tAc 'show server_version_num' | awk '{print int($1/10000)}')"
CLI_MAJOR="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
echo "Neon: ${SRV_MAJOR}, этот сервер: ${LOC_MAJOR}, pg_dump: ${CLI_MAJOR}"
if [ "$LOC_MAJOR" -lt "$SRV_MAJOR" ] || [ "$CLI_MAJOR" -lt "$SRV_MAJOR" ]; then
    echo "!! На сервере PostgreSQL старее, чем в Neon. Переустановите: PG_MAJOR=${SRV_MAJOR} bash deploy.sh"
    echo "   (если база на сервере ещё пустая — это безопасно; см. DEPLOY_HOME.md, шаг 3)"
    exit 1
fi

say "Остановка сайта на время копирования"
sudo systemctl stop pifagor

say "Копия из Neon"
DUMP="/var/backups/pifagor/neon-$(date +%F-%H%M).dump"
sudo mkdir -p /var/backups/pifagor; sudo chmod 700 /var/backups/pifagor
pg_dump --no-owner --no-privileges -Fc "$NEON_URL" | sudo tee "$DUMP" >/dev/null
echo "Файл копии: $DUMP ($(sudo du -h "$DUMP" | cut -f1)) — это ваша страховка, не удаляйте"

say "Загрузка на этот сервер"
PGPASS="$(sudo grep '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
sudo -u postgres dropdb --if-exists pifagor
sudo -u postgres createdb -O pifagor pifagor
sudo cat "$DUMP" | PGPASSWORD="$PGPASS" pg_restore --no-owner --no-privileges -h localhost -U pifagor -d pifagor 2>/tmp/pg_restore.log || true
ERR="$(grep -c 'error' /tmp/pg_restore.log || true)"
echo "Сообщений об ошибках при загрузке: $ERR (подробности: /tmp/pg_restore.log; ошибки про расширения Neon вроде neon/pg_stat_statements — нормально)"

say "Сверка числа строк во всех таблицах"
BAD=0
for t in $(q "select tablename from pg_tables where schemaname='public' order by 1"); do
    a="$(q "select count(*) from public.\"$t\"")"
    b="$(PGPASSWORD="$PGPASS" psql -h localhost -U pifagor -d pifagor -tAc "select count(*) from public.\"$t\"" 2>/dev/null || echo MISSING)"
    if [ "$a" != "$b" ]; then echo "  ✗ $t: Neon=$a, здесь=$b"; BAD=$((BAD+1)); fi
done
[ "$BAD" -eq 0 ] && echo "  ✓ Все таблицы совпадают" || { echo "!! Есть расхождения: $BAD. Сайт НЕ запущен. Пришлите вывод — разберёмся."; exit 1; }

say "Миграции (если в коде есть новее базы) и запуск"
sudo /usr/local/sbin/pifagor-manage migrate --noinput
sudo systemctl start pifagor
sleep 2
systemctl is-active --quiet pifagor && echo "Сайт запущен. Откройте его и проверьте вход учителя." || echo "!! Сайт не запустился: sudo journalctl -u pifagor -n 50"
