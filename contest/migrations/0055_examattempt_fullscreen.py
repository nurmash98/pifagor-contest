from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0054_coin_grant_kind'),
    ]

    operations = [
        migrations.AddField(model_name='examattempt', name='exits',
                            field=models.PositiveSmallIntegerField(default=0, verbose_name='Выходов из полного экрана')),
        migrations.AddField(model_name='examattempt', name='exit_log',
                            field=models.JSONField(blank=True, default=list, verbose_name='Журнал выходов')),
        migrations.AddField(model_name='examattempt', name='client',
                            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Браузер (сессия)')),
        migrations.AddField(model_name='examattempt', name='finish_reason',
                            field=models.CharField(blank=True, default='', max_length=16, verbose_name='Причина завершения')),
    ]
