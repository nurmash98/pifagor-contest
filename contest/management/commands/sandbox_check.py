"""Проверка «песочницы» для кода учеников: manage.py sandbox_check [--env-file путь].

Запускает маленькую программу так же, как автотесты, и смотрит, что она видит: переменные окружения
сервера, файл с паролями, локальную сеть и интернет. Запускается в конце установки (deploy.sh)."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from contest import autotest

PROBE = r'''
import json, os, socket
def tcp(host, port):
    s = socket.socket(); s.settimeout(0.7)
    try:
        s.connect((host, port)); return True
    except Exception:
        return False
    finally:
        s.close()
def readable(path):
    try:
        open(path, 'rb').read(1); return True
    except Exception:
        return False
print(json.dumps({
    'secret_env': sorted(k for k in os.environ if any(x in k for x in ('SECRET', 'POSTGRES', 'DJANGO', 'PASSWORD'))),
    'env_file': readable(%(env_file)r) if %(env_file)r else False,
    'loopback': tcp('127.0.0.1', 5432),
    'internet': tcp('1.1.1.1', 53),
}))
'''


class Command(BaseCommand):
    help = 'Проверяет изоляцию кода учеников (пользователь песочницы, файлы, сеть)'

    def add_arguments(self, parser):
        parser.add_argument('--env-file', default='', help='Путь к файлу с паролями (должен быть недоступен коду учеников)')

    def handle(self, *args, **opts):
        user = getattr(settings, 'AUTOTEST_SANDBOX_USER', '')
        if not user:
            raise CommandError('AUTOTEST_SANDBOX_USER не задан — код учеников выполняется без изоляции.')
        out, err = autotest._run_one(PROBE % {'env_file': opts['env_file']}, '')
        if err:
            raise CommandError(f'Не удалось запустить код в песочнице: {err}')
        r = json.loads(out)
        checks = [
            ('Пароли и ключи сервера не видны в окружении', not r['secret_env']),
            ('Файл с паролями недоступен', not r['env_file']),
            ('Нет доступа к локальным службам (127.0.0.1)', not r['loopback']),
            ('Нет доступа в интернет и домашнюю сеть', not r['internet']),
        ]
        bad = 0
        for title, ok in checks:
            self.stdout.write(('  ✓ ' if ok else '  ✗ ') + title)
            bad += (not ok)
        if bad:
            raise CommandError(f'Песочница настроена не полностью: проблем — {bad}. См. deploy/DEPLOY_HOME.md, раздел «Безопасность».')
        self.stdout.write(self.style.SUCCESS(f'Песочница ({user}) работает.'))
