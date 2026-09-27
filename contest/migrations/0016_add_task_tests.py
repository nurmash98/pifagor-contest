import json
from pathlib import Path

from django.db import migrations

TESTS_FILE = Path(__file__).resolve().parent.parent / 'data' / 'task_tests.json'


def add_tests(apps, schema_editor):
    """По 5 тест-кейсов для задач каталога (contest/data/task_tests.json, ключ — название
    задачи). Заполняем только задачи, у которых тестов ещё нет — тесты, которые админ уже
    добавил вручную, не трогаем."""
    Task = apps.get_model('contest', 'Task')
    tests_by_title = json.loads(TESTS_FILE.read_text(encoding='utf-8'))
    for task in Task.objects.filter(title__in=list(tests_by_title)):
        if not task.test_cases:
            task.test_cases = tests_by_title[task.title]
            task.save(update_fields=['test_cases'])


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0015_attempt_autograde'),
    ]

    operations = [
        migrations.RunPython(add_tests, migrations.RunPython.noop),
    ]
