from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('contest', '0040_cointransaction')]

    operations = [
        migrations.AddField(
            model_name='student', name='custom_banner',
            field=models.JSONField(blank=True, default=dict, verbose_name='Свой баннер'),
        ),
    ]
