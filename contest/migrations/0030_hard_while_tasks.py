"""Тема while, уровень C: 5 новых сложных задач (ввод — каждое значение на отдельной строке)."""
import json
import os

from django.db import migrations


def forward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    Tag = apps.get_model('contest', 'Tag')
    tag, _ = Tag.objects.get_or_create(name='while')
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'hard_while_tasks.json')
    with open(path, encoding='utf-8') as f:
        items = json.load(f)
    for item in items:
        if Task.objects.filter(title=item['title']).exists():
            continue
        task = Task.objects.create(
            title=item['title'], level='C', description=item['description'],
            input_example=item['input_example'], output_example=item['output_example'],
            test_cases=item['test_cases'], title_kk=item['title_kk'],
            description_kk=item['description_kk'],
            input_example_kk=item['input_example'], output_example_kk=item['output_example'])
        task.tags.add(tag)


def backward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'hard_while_tasks.json')
    with open(path, encoding='utf-8') as f:
        titles = [i['title'] for i in json.load(f)]
    Task.objects.filter(title__in=titles, level='C', tags__name='while').delete()


class Migration(migrations.Migration):

    dependencies = [("contest", "0029_while_input_per_line")]

    operations = [migrations.RunPython(forward, backward)]
