from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('contest', '0036_duel_mode_label')]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='duels_class_tags',
            field=models.JSONField(blank=True, default=dict, verbose_name='Теги дуэлей по классам'),
        ),
    ]
