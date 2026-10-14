"""Тема while, уровень A: задачи про цифры числа переводим в уровень B и добавляем
15 совсем лёгких задач на while (с тестами и казахским текстом)."""
import json
import os

from django.db import migrations

DIGIT_TASKS = ['Сумма цифр числа', 'Количество цифр числа', 'Разворот числа (while)', 'Первая цифра числа']


def forward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    Tag = apps.get_model('contest', 'Tag')
    Task.objects.filter(title__in=DIGIT_TASKS, level='A', tags__name='while').update(level='B')
    tag, _ = Tag.objects.get_or_create(name='while')
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'easy_while_tasks.json')
    with open(path, encoding='utf-8') as f:
        items = json.load(f)
    for item in items:
        if Task.objects.filter(title=item['title']).exists():
            continue
        task = Task.objects.create(
            title=item['title'], level='A', description=item['description'],
            input_example=item['input_example'], output_example=item['output_example'],
            test_cases=item['test_cases'], title_kk=item['title_kk'],
            description_kk=item['description_kk'],
            input_example_kk=item['input_example'], output_example_kk=item['output_example'])
        task.tags.add(tag)


def backward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'easy_while_tasks.json')
    with open(path, encoding='utf-8') as f:
        titles = [i['title'] for i in json.load(f)]
    Task.objects.filter(title__in=titles, level='A', tags__name='while').delete()
    Task.objects.filter(title__in=DIGIT_TASKS, level='B', tags__name='while').update(level='A')


class Migration(migrations.Migration):

    dependencies = [("contest", "0027_student_blocked_until")]

    operations = [migrations.RunPython(forward, backward)]
