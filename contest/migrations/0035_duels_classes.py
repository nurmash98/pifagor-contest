from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('contest', '0034_course_visible_classes')]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='duels_classes',
            field=models.CharField(blank=True, default='', max_length=500, verbose_name='Классы с доступом к дуэлям'),
        ),
    ]
