#!/usr/bin/env bash
# Деплой Pifagor Contest на Ubuntu (расчитано на Oracle Cloud Always Free VM).
# Запускать НА СЕРВЕРЕ, от имени обычного пользователя с доступом к sudo (например ubuntu).
#
# Перед первым запуском задайте переменные окружения:
#   export SERVER_IP=1.2.3.4
#   export POSTGRES_PASSWORD='придумайте-надёжный-пароль'
# и выполните: bash deploy/deploy.sh
#
# Повторный запуск (после git push новых изменений) безопасен — просто
# перезапустите этот же скрипт, он обновит код и перезапустит сервисы.

set -euo pipefail

REPO_URL="https://github.com/nurmash98/pifagor-contest.git"
BRANCH="feature/tags-and-courses"
APP_DIR="/opt/pifagor-contest"
APP_USER="$(whoami)"

echo "==> Обновление пакетов и установка зависимостей"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git nginx postgresql postgresql-contrib iptables-persistent

echo "==> Открываем порты 80 и 443 в iptables (Oracle по умолчанию блокирует всё, кроме SSH)"
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

echo "==> Клонирование/обновление репозитория в $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    sudo git -C "$APP_DIR" fetch origin "$BRANCH"
    sudo git -C "$APP_DIR" checkout "$BRANCH"
    sudo git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
    sudo mkdir -p "$APP_DIR"
    sudo chown "$APP_USER":"$APP_USER" "$APP_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
cd "$APP_DIR"

echo "==> Виртуальное окружение и зависимости"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r req.txt

echo "==> Настройка PostgreSQL"
: "${POSTGRES_PASSWORD:?Задайте переменную POSTGRES_PASSWORD перед запуском}"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='pifagor'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER pifagor WITH PASSWORD '${POSTGRES_PASSWORD}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='pifagor'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE pifagor OWNER pifagor;"

echo "==> Файл переменных окружения deploy/pifagor.env"
if [ ! -f "$APP_DIR/deploy/pifagor.env" ]; then
    : "${SERVER_IP:?Задайте переменную SERVER_IP перед первым запуском}"
    cat > "$APP_DIR/deploy/pifagor.env" <<EOF
DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=${SERVER_IP}
DJANGO_DB_ENGINE=postgres
POSTGRES_DB=pifagor
POSTGRES_USER=pifagor
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
EOF
    echo "Создан $APP_DIR/deploy/pifagor.env"
fi

echo "==> Миграции и статика"
set -a
source "$APP_DIR/deploy/pifagor.env"
set +a
python manage.py migrate
python manage.py collectstatic --noinput

echo "==> systemd-сервис gunicorn"
sudo cp deploy/gunicorn.service /etc/systemd/system/pifagor.service
sudo sed -i "s#__APP_DIR__#${APP_DIR}#g; s#__APP_USER__#${APP_USER}#g" /etc/systemd/system/pifagor.service
sudo systemctl daemon-reload
sudo systemctl enable pifagor
sudo systemctl restart pifagor

echo "==> Nginx"
sudo cp deploy/nginx.conf /etc/nginx/sites-available/pifagor
sudo sed -i "s#__APP_DIR__#${APP_DIR}#g" /etc/nginx/sites-available/pifagor
sudo ln -sf /etc/nginx/sites-available/pifagor /etc/nginx/sites-enabled/pifagor
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "==> Готово! Проверьте: http://${SERVER_IP:-<ваш-ip>}/"
echo "Если ещё не создан админ, выполните: cd $APP_DIR && source venv/bin/activate && source deploy/pifagor.env && python manage.py createsuperuser"
