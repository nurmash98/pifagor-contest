from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0057_task_groups_time_limit'),
    ]

    operations = [
        migrations.AddField(model_name='examanswer', name='language',
                            field=models.CharField(default='python', max_length=8, verbose_name='Язык')),
        migrations.AddField(model_name='examanswer', name='best_language',
                            field=models.CharField(default='python', max_length=8, verbose_name='Язык лучшего решения')),
        migrations.AddField(model_name='examanswer', name='compile_error',
                            field=models.TextField(blank=True, default='', verbose_name='Ошибка компиляции')),
    ]
