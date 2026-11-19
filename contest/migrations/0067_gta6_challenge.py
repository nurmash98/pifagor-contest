"""Челлендж GTA VI: тема «GTA VI» с 10 задачами (за 6 на 10/10 — баннер «Vice City» и аватар «Закат Vice City»)."""
import json
import os

from django.db import migrations

TAG = 'GTA VI'


def forward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    Tag = apps.get_model('contest', 'Tag')
    tag, _ = Tag.objects.get_or_create(name=TAG)
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


def backward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    Task.objects.filter(tags__name=TAG).delete()


class Migration(migrations.Migration):

    dependencies = [('contest', '0066_scratch_trainer')]

    operations = [migrations.RunPython(forward, backward)]
