from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0058_examanswer_language'),
    ]

    operations = [
        migrations.AddField(model_name='task', name='group_notes',
                            field=models.JSONField(blank=True, default=list, verbose_name='Ограничения групп')),
        migrations.AddField(model_name='task', name='group_notes_kk',
                            field=models.JSONField(blank=True, default=list, verbose_name='Ограничения групп (қазақша)')),
        migrations.AddField(model_name='task', name='examples',
                            field=models.JSONField(blank=True, default=list, verbose_name='Примеры')),
    ]
