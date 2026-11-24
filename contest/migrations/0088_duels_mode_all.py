from django.db import migrations


def forwards(apps, schema_editor):
    """Доступ «по классам» убран: дуэли теперь или для всех, или выключены."""
    apps.get_model('contest', 'SiteSettings').objects.filter(duels_mode='testers').update(duels_mode='all')


class Migration(migrations.Migration):
    dependencies = [('contest', '0087_student_last_seen')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
