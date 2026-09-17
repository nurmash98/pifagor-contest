# Деплой на Render + Neon (бесплатно, без Oracle)

Если с регистрацией в Oracle Cloud не получилось — этот вариант проще в
настройке (регистрация через GitHub/Google, без сложной верификации) и тоже
бесплатный. Компромисс: веб-сервис засыпает после 15 минут без запросов и
«просыпается» около минуты при следующем заходе — для школьного контеста
это обычно не критично.

Settings.py уже поддерживает всё нужное (PostgreSQL через переменные
окружения, WhiteNoise для раздачи статики) — отдельно ничего в коде менять
не придётся.

## 1. Бесплатная база данных на Neon

1. Зарегистрируйтесь на [neon.com](https://neon.com) (через GitHub).
2. Create a project → любое имя, регион — ближайший к Казахстану.
3. В Dashboard откройте **Connection Details** и запишите: host, database,
   user, password, port (обычно 5432). Neon требует SSL — это уже учтено
   в settings.py (`POSTGRES_SSLMODE`).

## 2. Веб-сервис на Render

1. Зарегистрируйтесь на [render.com](https://render.com) (через GitHub).
2. **New → Web Service** → подключите GitHub-репозиторий
   `nurmash98/pifagor-contest`, ветка `feature/tags-and-courses`.
3. Настройки сервиса:
   - **Environment**: Python 3
   - **Build Command**:
     ```
     pip install -r req.txt && python manage.py collectstatic --noinput && python manage.py migrate
     ```
   - **Start Command**:
     ```
     gunicorn pifagor_contest.wsgi:application
     ```
   - **Instance Type**: Free
4. **Environment Variables** (Environment → Add Environment Variable):
   ```
   DJANGO_SECRET_KEY      = <сгенерируйте случайную строку, например через https://djecrety.ir>
   DJANGO_DEBUG           = False
   DJANGO_ALLOWED_HOSTS   = <ваш-сервис>.onrender.com
   DJANGO_DB_ENGINE       = postgres
   POSTGRES_DB            = <из Neon>
   POSTGRES_USER          = <из Neon>
   POSTGRES_PASSWORD      = <из Neon>
   POSTGRES_HOST          = <из Neon>
   POSTGRES_PORT          = 5432
   POSTGRES_SSLMODE       = require
   ```
   `DJANGO_ALLOWED_HOSTS` заполните после первого деплоя, когда Render
   выдаст адрес вида `pifagor-contest.onrender.com` (можно сразу проставить
   этот шаблон, Render не меняет имя сервиса на лету).
5. **Create Web Service** — Render соберёт и задеплоит проект. Первый билд
   займёт пару минут; в логах будет видно `Running migrations...`.

## 3. Создание администратора

В интерфейсе Render откройте вкладку **Shell** у вашего сервиса и выполните:

```bash
python manage.py createsuperuser
```

(Если вкладки Shell нет на бесплатном тарифе — можно временно добавить
пользователя через Django-скрипт в Build Command разово, или создать
через код: подскажите, и я подготовлю такой вариант.)

## 4. Обновление после новых изменений

Просто `git push` в ветку `feature/tags-and-courses` — Render подхватывает
пуши в подключённую ветку и передеплоивает автоматически (Auto-Deploy
включён по умолчанию).

## 5. Ограничения бесплатного тарифа, о которых стоит помнить

- Сервис засыпает через 15 минут без запросов, первый заход после паузы —
  около минуты на «пробуждение».
- 750 часов инстанса в месяц на весь workspace — этого хватает на
  круглосуточную работу одного сервиса.
- Neon: 0.5 ГБ хранилища на бесплатном тарифе — с запасом хватает на код
  решений 500 учеников (это текст, не файлы).
