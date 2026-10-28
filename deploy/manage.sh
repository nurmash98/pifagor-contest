#!/usr/bin/env bash
# sudo pifagor-manage <команда>  — manage.py с настройками сайта, под пользователем pifagor.
# Примеры: sudo pifagor-manage createsuperuser | shell | migrate | check
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запускайте через sudo: sudo pifagor-manage <команда>"; exit 1; }
set -a; . /etc/pifagor/pifagor.env; set +a
install -d -o pifagor -g pifagor -m 700 /var/cache/pifagor
cd /opt/pifagor-contest
exec runuser -u pifagor -- /opt/pifagor-contest/venv/bin/python manage.py "$@"
