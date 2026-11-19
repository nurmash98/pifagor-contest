"""Челлендж GTA VI: 7 задач на ввод/вывод и if/else + 3 уровня C.
Три задачи на циклы (радио, небоскрёбы, сувениры) заменены на задачи с if/else."""
import json
import os

from django.db import migrations

TAG = 'GTA VI'
OLD = ['Радио в машине', 'Небоскрёбы', 'Сувениры']


def forward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    Tag = apps.get_model('contest', 'Tag')
    tag, _ = Tag.objects.get_or_create(name=TAG)
    Task.objects.filter(tags=tag, title__in=OLD).delete()
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'gta6_tasks.json')
    with open(path, encoding='utf-8') as f:
        items = json.load(f)
    for item in items:
        if Task.objects.filter(title=item['title'], tags=tag).exists():
            continue
        task = Task.objects.create(
            title=item['title'], level=item['level'], description=item['description'],
            input_example=item['input_example'], output_example=item['output_example'],
            test_cases=item['test_cases'], title_kk=item['title_kk'], description_kk=item['description_kk'],
            input_example_kk=item['input_example'], output_example_kk=item['output_example'])
        task.tags.add(tag)


class Migration(migrations.Migration):

    dependencies = [('contest', '0067_gta6_challenge')]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
