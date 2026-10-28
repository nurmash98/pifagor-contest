#!/usr/bin/env bash
# Установка (и повторная настройка) Pifagor Contest на своём Ubuntu-сервере (22.04 / 24.04).
# Запускать НА СЕРВЕРЕ от обычного пользователя с sudo. Безопасно запускать повторно.
#
#   DOMAIN=pifagor.example.kz bash deploy.sh            # домен можно указать позже
#   PG_MAJOR=17 bash deploy.sh                          # версия PostgreSQL = версии в Neon (см. DEPLOY_HOME.md)
#
# Необязательные переменные: DOMAIN, SERVER_IP (белый IP), PG_MAJOR, POSTGRES_PASSWORD,
# DJANGO_SECRET_KEY (возьмите со страницы Environment в Render — тогда ученики не разлогинятся).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/nurmash98/pifagor-contest.git}"
BRANCH="${BRANCH:-feature/tags-and-courses}"
APP_DIR=/opt/pifagor-contest
APP_USER=pifagor          # под ним работает сайт
BOX_USER=pifagorbox       # под ним запускается ТОЛЬКО код учеников (нет доступа ни к файлам сайта, ни к сети)
ENV_FILE=/etc/pifagor/pifagor.env
DOMAIN="${DOMAIN:-}"
SERVER_IP="${SERVER_IP:-}"
PG_MAJOR="${PG_MAJOR:-}"

say() { printf '\n==> %s\n' "$*"; }
[ "$(id -u)" -ne 0 ] || { echo "Запускайте от обычного пользователя (не root): bash deploy.sh"; exit 1; }
sudo -v

say "Часовой пояс Asia/Almaty"
sudo timedatectl set-timezone Asia/Almaty || true

say "Пакеты"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git nginx ufw curl ca-certificates openssl util-linux
if [ -n "$PG_MAJOR" ]; then
    # PostgreSQL нужной версии из официального репозитория PGDG (если в Ubuntu версия старее, чем в Neon)
    sudo apt-get install -y postgresql-common
    sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
    sudo apt-get install -y "postgresql-${PG_MAJOR}" "postgresql-client-${PG_MAJOR}"
else
    sudo apt-get install -y postgresql postgresql-contrib
fi
sudo systemctl enable --now postgresql

say "Пользователи ($APP_USER — сайт, $BOX_USER — код учеников)"
id "$APP_USER" >/dev/null 2>&1 || sudo useradd --system --no-create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
id "$BOX_USER" >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$BOX_USER"
# сайту разрешено запускать от имени $BOX_USER только prlimit (ограничитель ресурсов)
echo "$APP_USER ALL=($BOX_USER) NOPASSWD: /usr/bin/prlimit" | sudo tee /etc/sudoers.d/pifagor-sandbox >/dev/null
sudo chmod 440 /etc/sudoers.d/pifagor-sandbox
sudo visudo -cf /etc/sudoers.d/pifagor-sandbox >/dev/null

say "Фаервол"
sudo ufw allow from 10.0.0.0/8 to any port 22 proto tcp
sudo ufw allow from 172.16.0.0/12 to any port 22 proto tcp
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
# код учеников не имеет права выходить в сеть (ни в интернет, ни в домашнюю сеть, ни на localhost)
sudo cp "$(dirname "$0")/pifagor-sandbox-fw.service" /etc/systemd/system/pifagor-sandbox-fw.service
sudo sed -i "s#__BOX_USER__#${BOX_USER}#g" /etc/systemd/system/pifagor-sandbox-fw.service
sudo systemctl daemon-reload
sudo systemctl enable pifagor-sandbox-fw.service
sudo systemctl restart pifagor-sandbox-fw.service

say "Код проекта в $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo git config --system --get-all safe.directory | grep -qx "$APP_DIR" || sudo git config --system --add safe.directory "$APP_DIR"
if sudo test -d "$APP_DIR/.git"; then
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin "$BRANCH"
    sudo -u "$APP_USER" git -C "$APP_DIR" checkout "$BRANCH"
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
    sudo -u "$APP_USER" git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
sudo chmod 750 "$APP_DIR"          # $BOX_USER не может прочитать файлы сайта

say "Виртуальное окружение и зависимости"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/req.txt"

say "Файл настроек $ENV_FILE (читает только root; сайт получает значения от systemd)"
sudo mkdir -p /etc/pifagor
if ! sudo test -f "$ENV_FILE"; then
    PGPASS="${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"
    case "$PGPASS" in *[!A-Za-z0-9_.-]*) echo "POSTGRES_PASSWORD: только буквы, цифры и _ . -"; exit 1;; esac
    SECRET="${DJANGO_SECRET_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(60))')}"
    LAN_IP="$(hostname -I | awk '{print $1}')"
    HOSTS="localhost,127.0.0.1,${LAN_IP}"
    [ -z "$DOMAIN" ] || HOSTS="${DOMAIN},${HOSTS}"
    [ -z "$SERVER_IP" ] || HOSTS="${HOSTS},${SERVER_IP}"
    sudo tee "$ENV_FILE" >/dev/null <<EOT
DJANGO_SECRET_KEY=${SECRET}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=${HOSTS}
DJANGO_DB_ENGINE=postgres
POSTGRES_DB=pifagor
POSTGRES_USER=pifagor
POSTGRES_PASSWORD=${PGPASS}
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_SSLMODE=disable
DJANGO_CACHE_DIR=/var/cache/pifagor
TRUST_X_REAL_IP=True
AUTOTEST_SANDBOX_USER=${BOX_USER}
AUTOTEST_SANDBOX_PYTHON=/usr/bin/python3
DUELS_ADMIN_USERNAMES=nurmash
# Остальные переменные со страницы Environment в Render (если были, например DJANGO_REGISTRATION_OPEN,
# HOLIDAY_FORCE) допишите сюда, затем: sudo systemctl restart pifagor
EOT
    echo "Создан $ENV_FILE"
fi
sudo chmod 600 "$ENV_FILE"; sudo chown root:root "$ENV_FILE"
PGPASS="$(sudo grep '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"

say "База данных PostgreSQL"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${APP_USER}'" | grep -q 1 \
    && sudo -u postgres psql -qc "ALTER USER ${APP_USER} WITH PASSWORD '${PGPASS}';" \
    || sudo -u postgres psql -qc "CREATE USER ${APP_USER} WITH PASSWORD '${PGPASS}';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='pifagor'" | grep -q 1 \
    || sudo -u postgres createdb -O "$APP_USER" pifagor

say "Служебные скрипты (копии принадлежат root — сайт не может их подменить)"
for f in update.sh backup.sh manage.sh; do
    sudo install -m 755 -o root -g root "$APP_DIR/deploy/$f" "/usr/local/sbin/pifagor-${f%.sh}"
done
sudo mkdir -p /var/backups/pifagor
sudo cp "$APP_DIR"/deploy/pifagor-backup.{service,timer} "$APP_DIR"/deploy/pifagor-update.{service,timer} /etc/systemd/system/

sudo install -d -o "$APP_USER" -g "$APP_USER" -m 700 /var/cache/pifagor

say "Миграции и статика"
sudo /usr/local/sbin/pifagor-manage migrate --noinput
sudo /usr/local/sbin/pifagor-manage collectstatic --noinput

say "Сервис сайта (gunicorn)"
sudo cp "$APP_DIR/deploy/gunicorn.service" /etc/systemd/system/pifagor.service
sudo systemctl daemon-reload
sudo systemctl enable pifagor pifagor-backup.timer
sudo systemctl restart pifagor
sudo systemctl start pifagor-backup.timer

say "Nginx"
sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/pifagor
sudo sed -i "s#__SERVER_NAME__#${DOMAIN:-_}#g" /etc/nginx/sites-available/pifagor
sudo ln -sf /etc/nginx/sites-available/pifagor /etc/nginx/sites-enabled/pifagor
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl reload nginx || sudo systemctl restart nginx

say "Проверка изоляции кода учеников"
sudo /usr/local/sbin/pifagor-manage sandbox_check --env-file "$ENV_FILE" || \
    echo "!! Песочница работает не полностью — см. раздел «Безопасность» в DEPLOY_HOME.md"

say "Готово"
echo "Сайт:  http://$(hostname -I | awk '{print $1}')/   (из домашней сети)"
echo "Создать администратора:  sudo pifagor-manage createsuperuser"
echo "Обновление сайта:        sudo pifagor-update"
echo "Автообновление из GitHub (как в Render):  sudo systemctl enable --now pifagor-update.timer"
