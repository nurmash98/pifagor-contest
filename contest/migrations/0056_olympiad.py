from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0055_examattempt_fullscreen'),
    ]

    operations = [
        migrations.AlterField(model_name='exam', name='kind',
                              field=models.CharField(choices=[('SOR', 'СОР'), ('SOCH', 'СОЧ'), ('OLYM', 'Олимпиада')],
                                                     default='SOR', max_length=4, verbose_name='Тип')),
        migrations.AddField(model_name='exam', name='participants',
                            field=models.ManyToManyField(blank=True, related_name='olympiads', to='contest.student',
                                                         verbose_name='Участники олимпиады')),
        migrations.AddField(model_name='task', name='hidden',
                            field=models.BooleanField(default=False, verbose_name='Скрыта (только для олимпиады)')),
    ]
