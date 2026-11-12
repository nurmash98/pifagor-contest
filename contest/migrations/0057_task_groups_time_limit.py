from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0056_olympiad'),
    ]

    operations = [
        migrations.AddField(model_name='task', name='test_groups',
                            field=models.JSONField(blank=True, default=list, verbose_name='Баллы групп тестов')),
        migrations.AddField(model_name='task', name='time_limit',
                            field=models.PositiveSmallIntegerField(default=1, verbose_name='Лимит времени на тест (сек)')),
    ]
