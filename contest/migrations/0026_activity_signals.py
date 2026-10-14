from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contest", "0025_exam_retake"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt", name="key_count",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Нажатий клавиш"),
        ),
        migrations.AddField(
            model_name="attempt", name="pointer_count",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Движений мыши/касаний"),
        ),
        migrations.CreateModel(
            name="LoginEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("at", models.DateTimeField(auto_now_add=True)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=300)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="login_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-at"]},
        ),
    ]
