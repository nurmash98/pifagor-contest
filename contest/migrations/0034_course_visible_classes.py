from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('contest', '0033_duel_task_timer')]

    operations = [
        migrations.AddField(
            model_name='course',
            name='visible_classes',
            field=models.CharField(blank=True, help_text='Через запятую, например: 7F, 10A. Пусто — виден всем классам', max_length=255, verbose_name='Классы, которым виден курс'),
        ),
    ]
