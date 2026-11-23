from django.db import migrations


def forwards(apps, schema_editor):
    """Удалить тренировочные олимпиады («Подготовка к районным олимпиадам») и их скрытые задачи."""
    Exam = apps.get_model('contest', 'Exam')
    Task = apps.get_model('contest', 'Task')
    exams = Exam.objects.filter(practice=True)
    task_ids = list(Task.objects.filter(exams__in=exams, hidden=True).values_list('id', flat=True).distinct())
    exams.delete()
    Task.objects.filter(id__in=task_ids, exams__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('contest', '0077_district_olympiads')]
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        # RemoveField — отдельной миграцией 0079: в PostgreSQL нельзя ALTER TABLE в той же транзакции,
        # где удаление строк оставило отложенные проверки (pending trigger events).
    ]
