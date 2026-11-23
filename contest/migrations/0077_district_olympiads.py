from django.db import migrations


def forwards(apps, schema_editor):
    from contest.district_olympiads import load
    load(apps.get_model('contest', 'Exam'), apps.get_model('contest', 'Task'))


class Migration(migrations.Migration):
    dependencies = [('contest', '0076_exam_practice')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
