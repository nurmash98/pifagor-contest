from django.db import migrations, models


def to_classes(apps, schema_editor):
    """Раньше игра открывалась отдельным ученикам; теперь — классам. Берём классы, где были выбранные ученики."""
    CodeGame = apps.get_model('contest', 'CodeGame')
    for g in CodeGame.objects.all():
        names = sorted({s.school_class for s in g.students.all() if s.school_class})
        if names:
            g.classes = ',' + ','.join(names) + ','
            g.save(update_fields=['classes'])
            g.students.clear()


class Migration(migrations.Migration):

    dependencies = [('contest', '0060_exam_quarter')]

    operations = [
        migrations.AddField(model_name='codegame', name='classes',
                            field=models.CharField(blank=True, default='', max_length=300, verbose_name='Классы')),
        migrations.AlterField(model_name='codegame', name='students',
                              field=models.ManyToManyField(blank=True, related_name='code_games', to='contest.student',
                                                           verbose_name='Ученики (старый выбор)')),
        migrations.RunPython(to_classes, migrations.RunPython.noop),
    ]
