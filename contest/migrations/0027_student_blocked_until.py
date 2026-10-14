from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("contest", "0026_activity_signals")]

    operations = [
        migrations.AddField(
            model_name="student", name="blocked_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Заблокирован до"),
        ),
    ]
