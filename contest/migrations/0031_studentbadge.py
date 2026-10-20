from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [("contest", "0030_hard_while_tasks")]

    operations = [
        migrations.CreateModel(
            name="StudentBadge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=40)),
                ("awarded_at", models.DateTimeField()),
                ("seen", models.BooleanField(default=False)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="badges", to="contest.student")),
            ],
            options={"ordering": ["awarded_at"]},
        ),
        migrations.AddConstraint(
            model_name="studentbadge",
            constraint=models.UniqueConstraint(fields=("student", "key"), name="unique_student_badge"),
        ),
    ]
