import json
from pathlib import Path

from django.db import migrations

DATA_FILE = Path(__file__).resolve().parent.parent / 'data' / 'english_answers.json'


def english_answers(apps, schema_editor):
    """Ответы на английском, одинаковые для RU и KZ (contest/data/english_answers.json):
    раньше в KZ-версии пример ответа был по-казахски, а тесты ждали русский текст —
    ученик с языком KZ получал 0 за верное решение. Теперь в 63 задачах все фиксированные
    ответы — английские (YES/NO, короткие слова) или просто числа; в 4 задачах слова-коды
    во вводе заменены числами. Меняем задачу, только если её условие ещё исходное (правки
    учителя не трогаем), и тесты — только если они исходные."""
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
        ('contest', '0017_standardize_tasks'),
    ]

    operations = [
        migrations.RunPython(english_answers, migrations.RunPython.noop),
    ]
