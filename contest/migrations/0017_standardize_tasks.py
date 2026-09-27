import json
from pathlib import Path

from django.db import migrations

DATA_FILE = Path(__file__).resolve().parent.parent / 'data' / 'standardize_tasks.json'


def standardize(apps, schema_editor):
    """Стандартизация задач (contest/data/standardize_tasks.json, ключ — название):
    - 6 задач без тестов: однозначный текст, ответы на английском (YES/NO и т.п.),
      ввод только числами — и к ним добавляются 5 тестов;
    - 9 лёгких задач с тегом input/output: каждое число вводится на отдельной строке
      (пример и тесты тоже).
    Задачу меняем, только если её условие ещё совпадает с исходным — то, что учитель
    уже успел отредактировать сам, не трогаем. Тесты заменяем, только если они
    исходные (или их нет вовсе)."""
    Task = apps.get_model('contest', 'Task')
    for entry in json.loads(DATA_FILE.read_text(encoding='utf-8')):
        task = Task.objects.filter(title=entry['title']).first()
        if task is None or task.description != entry['old_description']:
            continue
        for field, value in entry['fields'].items():
            setattr(task, field, value)
        old_tests = entry['old_test_cases']
        if (old_tests is None and not task.test_cases) or (old_tests is not None and task.test_cases == old_tests):
            task.test_cases = entry['test_cases']
        task.save()


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0016_add_task_tests'),
    ]

    operations = [
        migrations.RunPython(standardize, migrations.RunPython.noop),
    ]
