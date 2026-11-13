from django.db import migrations, models


def fill(apps, schema_editor):
    """Уже созданным СОР/СОЧ четверть ставится по дате проведения."""
    from contest import quarters
    Exam = apps.get_model('contest', 'Exam')
    for exam in Exam.objects.exclude(kind='OLYM').filter(quarter__isnull=True):
        q = quarters.current(exam.start_at)
        exam.quarter = q['n'] if q else 4
        exam.save(update_fields=['quarter'])


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0059_task_examples_group_notes'),
    ]

    operations = [
        migrations.AddField(model_name='exam', name='quarter',
                            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Четверть')),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
