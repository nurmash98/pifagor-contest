# Pifagor Contest на Mac-сервере → https://pifagor.infosabaq.kz

Сайт работает на том же Mac, что и infosabaq.kz, а база пока остаётся в **Neon**.

```
браузер → Cloudflare (HTTPS) → туннель infosabaq → gunicorn 127.0.0.1:8090 → Neon
```

## Установка (один раз, на Mac-сервере)
Откройте «Терминал» на Mac-сервере и выполните:
```bash
curl -fsSL https://raw.githubusercontent.com/nurmash98/pifagor-contest/feature/tags-and-courses/deploy/macos/setup.sh -o /tmp/pifagor-setup.sh && bash /tmp/pifagor-setup.sh
```
Скрипт спросит:
1. **DATABASE_URL** — строка подключения из Neon (Connect → Connection string). Ввод скрыт; строка сохраняется только в `~/pifagor-data/pifagor.env` на этом Mac.
2. **DJANGO_SECRET_KEY** — из Render (Environment). Тогда никого не выкинет из аккаунтов. Можно нажать Enter — будет новый ключ.
3. **Пароль Mac** (sudo) — для песочницы: код учеников запускается под отдельным пользователем `pifagorbox`, без доступа к файлам сайта и к интернету.

В конце скрипт проверяет песочницу (все строки должны быть «OK») и сайт.

## Что работает само
| Что | Как |
|---|---|
| Сайт 24/7, после перезагрузки тоже | launchd `kz.pifagor.web` |
| Обновление после `git push` (как в Render) | раз в минуту, `kz.pifagor.update`; при ошибке откат |
| HTTPS | Cloudflare (туннель infosabaq, правило `pifagor.infosabaq.kz → :8090`) |

## Полезные команды
| Что | Команда |
|---|---|
| Лог сайта | `tail -n 50 ~/pifagor-data/web.log` |
| Лог обновлений | `tail -n 20 ~/pifagor-data/update.log` |
| Перезапуск | `launchctl kickstart -k gui/$(id -u)/kz.pifagor.web` |
| Обновить сейчас | `FORCE=1 bash ~/srv/pifagor-contest/deploy/macos/update.sh` |
| Настройки | `open -e ~/pifagor-data/pifagor.env` (потом перезапуск) |
| Любая команда manage.py | `bash ~/srv/pifagor-contest/deploy/macos/manage.sh <команда>` |

## Render
Пока оба сайта (Render и Mac) работают с одной базой Neon — это нормально. Когда pifagor.infosabaq.kz проверен,
в Render нажмите **Suspend** — экономия $25 в месяц. Neon оставьте: база в нём.
