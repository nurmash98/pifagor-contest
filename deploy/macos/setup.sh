#!/bin/bash
# Pifagor Contest на Mac-сервере (тот же Mac, где работает infosabaq.kz):
#   https://pifagor.infosabaq.kz → Cloudflare Tunnel → gunicorn 127.0.0.1:8090 → база Neon.
# Запуск (один раз; можно повторять — сделанное пропускается):
#   curl -fsSL https://raw.githubusercontent.com/nurmash98/pifagor-contest/feature/tags-and-courses/deploy/macos/setup.sh -o /tmp/pifagor-setup.sh && bash /tmp/pifagor-setup.sh
# Строку подключения Neon вы вводите сами в этом окне — она сохраняется только на этом Mac.
set -euo pipefail
REPO="https://github.com/nurmash98/pifagor-contest.git"
BRANCH="feature/tags-and-courses"
PROJECT="$HOME/srv/pifagor-contest"
DOMAIN="${PIFAGOR_DOMAIN:-pifagor.infosabaq.kz}"
PORT="${PIFAGOR_PORT:-8090}"
BOX="pifagorbox"                                  # пользователь-песочница для кода учеников
RUNDIR="/Users/Shared/pifagorbox-run"
PYDIR="/Users/Shared/pifagor-python"
AGENTS="$HOME/Library/LaunchAgents"
ME="$(whoami)"
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

step "1/9 Код из GitHub → $PROJECT"
mkdir -p "$HOME/srv" "$AGENTS"
if [ -d "$PROJECT/.git" ]; then
  git -C "$PROJECT" fetch -q origin "$BRANCH" && git -C "$PROJECT" checkout -q "$BRANCH" && git -C "$PROJECT" merge --ff-only -q "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO" "$PROJECT"
fi
cd "$PROJECT"
source deploy/macos/env.sh
mkdir -p "$PIFAGOR_DATA/cache"; chmod 700 "$PIFAGOR_DATA"

step "2/9 Python 3.12 и библиотеки"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
[ -x venv/bin/python ] || uv venv -q --python 3.12 venv
uv pip install -q --python venv/bin/python -r req.txt

step "3/9 Python для кода учеников (в $PYDIR — доступен песочнице)"
if ! ls "$PYDIR"/cpython-3.12*/bin/python3.12 >/dev/null 2>&1; then
  sudo mkdir -p "$PYDIR" && sudo chown "$ME" "$PYDIR"
  UV_PYTHON_INSTALL_DIR="$PYDIR" uv python install -q 3.12
  chmod -R a+rX "$PYDIR"
fi
BOXPY="$(ls -d "$PYDIR"/cpython-3.12*/bin/python3.12 | head -1)"
echo "Python песочницы: $BOXPY"

step "4/9 Настройки ($PIFAGOR_ENV)"
if [ ! -f "$PIFAGOR_ENV" ]; then
  echo "Вставьте строку подключения Neon (Neon → Connect → Connection string, вида postgresql://...)."
  echo "Ввод скрыт — после вставки просто нажмите Enter."
  read -rs -p "DATABASE_URL: " DBURL; echo
  case "$DBURL" in postgres://*|postgresql://*) ;; *) echo "Это не похоже на строку Neon. Запустите скрипт снова."; exit 1 ;; esac
  echo "Вставьте DJANGO_SECRET_KEY из Render (Environment) — тогда никого не выкинет из аккаунтов."
  read -rs -p "DJANGO_SECRET_KEY (Enter — создать новый): " SKEY; echo
  [ -n "$SKEY" ] || SKEY="$(venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(50))')"
  umask 077
  cat > "$PIFAGOR_ENV" <<ENV
# Pifagor Contest — настройки сервера. Файл только для этого Mac, в git не попадает.
DATABASE_URL=$DBURL
DJANGO_SECRET_KEY=$SKEY
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=$DOMAIN,127.0.0.1,localhost
DJANGO_HTTPS_PROXY=True
DJANGO_CACHE_DIR=$PIFAGOR_DATA/cache
PIFAGOR_BIND=127.0.0.1:$PORT
AUTOTEST_SANDBOX_USER=$BOX
AUTOTEST_SANDBOX_PYTHON=$BOXPY
AUTOTEST_TMP_DIR=$RUNDIR
# Остальные переменные из Render (если были) допишите ниже, например:
# DJANGO_REGISTRATION_OPEN=False
# DUELS_ADMIN_USERNAMES=nurmash
ENV
  umask 022
  echo "Сохранено. Изменить можно позже: open -e $PIFAGOR_ENV"
fi
load_env "$PIFAGOR_ENV"

step "5/9 Песочница: пользователь $BOX без доступа к файлам сайта и к интернету (нужен пароль Mac)"
if ! id "$BOX" >/dev/null 2>&1; then
  UIDN=599; while dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$UIDN"; do UIDN=$((UIDN-1)); done
  sudo dscl . -create "/Users/$BOX"
  sudo dscl . -create "/Users/$BOX" UniqueID "$UIDN"
  sudo dscl . -create "/Users/$BOX" PrimaryGroupID -2
  sudo dscl . -create "/Users/$BOX" UserShell /usr/bin/false
  sudo dscl . -create "/Users/$BOX" NFSHomeDirectory /var/empty
  sudo dscl . -create "/Users/$BOX" RealName "Pifagor sandbox"
  sudo dscl . -create "/Users/$BOX" IsHidden 1
  sudo dscl . -create "/Users/$BOX" Password '*'
fi
SUDOERS="/etc/sudoers.d/pifagor-sandbox"
echo "$ME ALL=($BOX) NOPASSWD: ALL" > /tmp/pifagor-sudoers
sudo visudo -cf /tmp/pifagor-sudoers >/dev/null && sudo install -m 440 -o root -g wheel /tmp/pifagor-sudoers "$SUDOERS"; rm -f /tmp/pifagor-sudoers
mkdir -p "$RUNDIR"; chmod 755 "$RUNDIR"
# домашняя папка на macOS открыта на чтение всем — закрываем то, где лежат секреты и код
for d in "$HOME/srv" "$PIFAGOR_DATA" "$HOME/.cloudflared" "$HOME/infosabaq-data" "$HOME/.ssh"; do if [ -d "$d" ]; then chmod 700 "$d"; fi; done
# интернет для пользователя песочницы закрыт правилом pf (переживает перезагрузку)
echo "block drop out quick all user $BOX" | sudo tee /etc/pf.anchors/pifagor >/dev/null
sudo tee /Library/LaunchDaemons/kz.pifagor.pf.plist >/dev/null <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>kz.pifagor.pf</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
<string>/sbin/pfctl -E; /sbin/pfctl -a com.apple/pifagor -f /etc/pf.anchors/pifagor</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
PL
sudo launchctl bootout system/kz.pifagor.pf 2>/dev/null || true
sudo launchctl bootstrap system /Library/LaunchDaemons/kz.pifagor.pf.plist || true
sleep 1

step "6/9 Проверка песочницы"
ok=1
r="$(sudo -n -u "$BOX" -- "$BOXPY" -I -c 'print(6*7)' 2>&1)" && [ "$r" = 42 ] && echo "  OK  код запускается" || { echo "  !!  код не запускается: $r"; ok=0; }
sudo -n -u "$BOX" -- "$BOXPY" -I -c "open('$PIFAGOR_ENV').read()" >/dev/null 2>&1 && { echo "  !!  песочница видит настройки сайта"; ok=0; } || echo "  OK  настройки сайта закрыты"
sudo -n -u "$BOX" -- "$BOXPY" -I -c "import os; os.listdir('$PROJECT')" >/dev/null 2>&1 && { echo "  !!  песочница видит код сайта"; ok=0; } || echo "  OK  код сайта закрыт"
if [ -d "$HOME/.cloudflared" ]; then
  sudo -n -u "$BOX" -- "$BOXPY" -I -c "import os; os.listdir('$HOME/.cloudflared')" >/dev/null 2>&1 && { echo "  !!  песочница видит ключи Cloudflare"; ok=0; } || echo "  OK  ключи Cloudflare закрыты"
fi
sudo -n -u "$BOX" -- "$BOXPY" -I -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)" >/dev/null 2>&1 && { echo "  !!  у песочницы есть интернет"; ok=0; } || echo "  OK  интернет закрыт"
[ "$ok" = 1 ] || echo "Песочница настроена не полностью — сообщите об этом (сайт всё равно установится)."
command -v clang++ >/dev/null 2>&1 || command -v g++ >/dev/null 2>&1 || { echo "Для C++ нужен компилятор: xcode-select --install"; }

step "7/9 База Neon: проверка и миграции"
venv/bin/python manage.py check --deploy 2>/dev/null | tail -n 1 || true
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py collectstatic --noinput -v0

step "8/9 Автозапуск 24/7 и автообновление из GitHub (раз в минуту)"
cat > "$AGENTS/kz.pifagor.web.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>kz.pifagor.web</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$PROJECT/deploy/macos/run-web.sh</string></array>
<key>WorkingDirectory</key><string>$PROJECT</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$PIFAGOR_DATA/web.log</string>
<key>StandardErrorPath</key><string>$PIFAGOR_DATA/web.log</string>
</dict></plist>
PL
cat > "$AGENTS/kz.pifagor.update.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>kz.pifagor.update</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$PROJECT/deploy/macos/update.sh</string></array>
<key>StartInterval</key><integer>60</integer>
<key>StandardOutPath</key><string>$PIFAGOR_DATA/update.log</string>
<key>StandardErrorPath</key><string>$PIFAGOR_DATA/update.log</string>
</dict></plist>
PL
for s in web update; do
  launchctl bootout "gui/$(id -u)/kz.pifagor.$s" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/kz.pifagor.$s.plist"
done

step "9/9 Cloudflare Tunnel: $DOMAIN → 127.0.0.1:$PORT"
CFG="$HOME/.cloudflared/config.yml"
if [ -f "$CFG" ]; then
  if ! grep -q "hostname: $DOMAIN" "$CFG"; then
    cp "$CFG" "$CFG.bak-pifagor"
    venv/bin/python - "$CFG" "$DOMAIN" "$PORT" <<'PY'
import sys
p, host, port = sys.argv[1:]
lines = open(p).read().split('\n')
i = lines.index('ingress:')
lines[i + 1:i + 1] = [f'  - hostname: {host}', f'    service: http://127.0.0.1:{port}']
open(p, 'w').write('\n'.join(lines))
PY
  fi
  launchctl kickstart -k "gui/$(id -u)/kz.infosabaq.tunnel" 2>/dev/null || true
else
  echo "Не найден $CFG — туннель infosabaq ещё не настроен (deploy/setup-tunnel.sh в infosabaq)."
fi

sleep 6
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' "http://127.0.0.1:$PORT/login/" || true)"
echo
if [ "$code" = 200 ]; then echo "Сайт работает локально (HTTP 200)."; else echo "Сайт ответил: $code — смотрите лог: tail -n 50 $PIFAGOR_DATA/web.log"; fi
cat <<MSG

Готово: https://$DOMAIN
  • Обновление из GitHub — автоматически раз в минуту (лог: $PIFAGOR_DATA/update.log)
  • Лог сайта: $PIFAGOR_DATA/web.log
  • Настройки: $PIFAGOR_ENV (после изменения: launchctl kickstart -k gui/\$(id -u)/kz.pifagor.web)
MSG
