# Деплой на Oracle Cloud Always Free

Инструкция по развёртыванию Pifagor Contest на бесплатной виртуальной машине
Oracle Cloud (Ampere A1, Ubuntu) с PostgreSQL, Gunicorn и Nginx.

## 1. Создание сервера (один раз, вручную в консоли OCI)

1. Зарегистрируйтесь на [cloud.oracle.com](https://cloud.oracle.com) (Always Free tier).
2. Compute → Instances → **Create Instance**:
   - Image: **Ubuntu** (последняя LTS).
   - Shape → Change shape → **Ampere** → `VM.Standard.A1.Flex`, 4 OCPU / 24 GB (максимум по Always Free).
   - SSH keys: вставьте свой публичный ключ (Paste public keys).
3. Откройте порты 80 и 443: инстанс → Primary VNIC → Subnet → Security List → **Add Ingress Rules**:
   - `0.0.0.0/0`, TCP, порт `80`
   - `0.0.0.0/0`, TCP, порт `443`
4. Запишите Public IP инстанса.

## 2. Подключение по SSH

```bash
ssh -i /путь/к/приватному_ключу ubuntu@<PUBLIC_IP>
```

## 3. Первый деплой

На сервере:

```bash
export SERVER_IP=<PUBLIC_IP>
export POSTGRES_PASSWORD='придумайте-надёжный-пароль'

git clone --branch feature/tags-and-courses https://github.com/nurmash98/pifagor-contest.git /tmp/pifagor-bootstrap
bash /tmp/pifagor-bootstrap/deploy/deploy.sh
```

Скрипт `deploy/deploy.sh`:
- ставит Python/Nginx/PostgreSQL;
- открывает порты 80/443 в iptables (в Oracle-образах Ubuntu по умолчанию заблокировано всё, кроме SSH — это отдельно от Security List в консоли, важно сделать оба шага);
- клонирует проект в `/opt/pifagor-contest`;
- создаёт venv и ставит зависимости из `req.txt`;
- создаёт пользователя и базу PostgreSQL;
- генерирует `deploy/pifagor.env` со случайным `SECRET_KEY` (при первом запуске — дальше файл не перезаписывается);
- накатывает миграции и собирает статику;
- настраивает `systemd`-сервис `pifagor` (Gunicorn) и Nginx.

После первого запуска создайте администратора:

```bash
cd /opt/pifagor-contest
source venv/bin/activate
source deploy/pifagor.env
python manage.py createsuperuser
```

Откройте `http://<PUBLIC_IP>/` в браузере.

## 4. Обновление после новых изменений (git push)

```bash
export SERVER_IP=<PUBLIC_IP>
export POSTGRES_PASSWORD='тот же пароль, что и раньше'
bash /opt/pifagor-contest/deploy/deploy.sh
```

Скрипт безопасно перезапускать — он подтянет новый код (`git reset --hard origin/feature/tags-and-courses`), применит новые миграции и перезапустит Gunicorn и Nginx. `deploy/pifagor.env` при этом не трогается, если уже существует.

## 5. Полезные команды на сервере

```bash
# Логи приложения (Gunicorn/Django)
sudo journalctl -u pifagor -f

# Логи Nginx
sudo tail -f /var/log/nginx/error.log

# Перезапустить приложение вручную
sudo systemctl restart pifagor

# Зайти в PostgreSQL
sudo -u postgres psql pifagor
```

## 6. Домен и HTTPS (когда появится домен)

1. Настройте A-запись домена на `<PUBLIC_IP>`.
2. Добавьте домен в `DJANGO_ALLOWED_HOSTS` в `deploy/pifagor.env` и перезапустите `pifagor`.
3. Установите Certbot и получите бесплатный сертификат Let's Encrypt:
   ```bash
   sudo apt-get install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d ваш-домен.kz
   ```
   Certbot сам пропишет HTTPS в конфиг Nginx и настроит автопродление.

## 7. О базе данных

Проект использует PostgreSQL на сервере (переменная `DJANGO_DB_ENGINE=postgres` в
`deploy/pifagor.env`) и SQLite локально при разработке (когда эта переменная не
задана) — так, что `runserver` на вашем компьютере продолжает работать как раньше,
без каких-либо изменений в привычном рабочем процессе.
