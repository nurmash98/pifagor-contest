from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contest", "0024_exam_random_slots"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamRetake",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_at", models.DateTimeField(verbose_name="Начало пересдачи")),
                ("duration_minutes", models.PositiveIntegerField(verbose_name="Длительность (минут)")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="exam_retakes", to="contest.teacher", verbose_name="Кто назначил")),
                ("exam", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="retakes",
                    to="contest.exam", verbose_name="Экзамен")),
                ("students", models.ManyToManyField(
                    related_name="exam_retakes", to="contest.student", verbose_name="Ученики")),
            ],
            options={
                "verbose_name": "Пересдача СОР/СОЧ",
                "verbose_name_plural": "Пересдачи СОР/СОЧ",
                "ordering": ["start_at"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="examattempt",
            unique_together=set(),
        ),
        migrations.AddField(
            model_name="examattempt",
            name="retake",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name="attempts", to="contest.examretake", verbose_name="Пересдача"),
        ),
        migrations.AddConstraint(
            model_name="examattempt",
            constraint=models.UniqueConstraint(
                condition=models.Q(("retake__isnull", True)), fields=("exam", "student"),
                name="unique_exam_attempt"),
        ),
        migrations.AddConstraint(
            model_name="examattempt",
            constraint=models.UniqueConstraint(
                condition=models.Q(("retake__isnull", False)), fields=("retake", "student"),
                name="unique_retake_attempt"),
        ),
    ]
