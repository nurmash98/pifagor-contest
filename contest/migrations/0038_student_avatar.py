from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('contest', '0037_duels_class_tags')]

    operations = [
        migrations.AddField(
            model_name='student',
            name='avatar',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='Аватар'),
        ),
    ]
