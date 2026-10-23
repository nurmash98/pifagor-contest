from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [('contest', '0039_republic_day')]

    operations = [
        migrations.CreateModel(
            name='CoinTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=60)),
                ('kind', models.CharField(choices=[('task', 'Задача 10/10'), ('day', 'Первая задача дня'), ('milestone', 'Каждые 50 задач'), ('exam', 'СОР/СОЧ на 10'), ('buy', 'Покупка')], max_length=20)),
                ('amount', models.IntegerField()),
                ('note', models.CharField(blank=True, max_length=200)),
                ('at', models.DateTimeField()),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='coin_transactions', to='contest.student')),
            ],
            options={'verbose_name': 'Монеты', 'verbose_name_plural': 'Монеты', 'ordering': ['-at', '-id'],
                     'constraints': [models.UniqueConstraint(fields=('student', 'key'), name='unique_student_coin_key')]},
        ),
    ]
