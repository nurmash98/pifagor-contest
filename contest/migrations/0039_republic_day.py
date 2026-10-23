"""День Республики: тема «Республика күні» с задачами по истории Казахстана и баннеры под именем ученика."""
import json
import os

from django.db import migrations, models

TAG = 'Республика күні'


def forward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    Tag = apps.get_model('contest', 'Tag')
    tag, _ = Tag.objects.get_or_create(name=TAG)
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'republic_tasks.json')
    with open(path, encoding='utf-8') as f:
        items = json.load(f)
    for item in items:
        if Task.objects.filter(title=item['title']).exists():
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

    dependencies = [('contest', '0038_student_avatar')]

    operations = [
        migrations.AddField(
            model_name='student', name='name_banner',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='Баннер под именем'),
        ),
        migrations.AddField(
            model_name='student', name='owned_banners',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Полученные баннеры'),
        ),
        migrations.RunPython(forward, backward),
    ]
