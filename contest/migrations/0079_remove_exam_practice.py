from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('contest', '0078_remove_district_olympiads')]
    operations = [
        migrations.RemoveField(model_name='exam', name='practice'),
    ]
