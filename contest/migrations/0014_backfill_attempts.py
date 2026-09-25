from django.db import migrations


def backfill(apps, schema_editor):
    """Существующие отправки → попытка #1 (до этой миграции хранился только последний код
    и последняя оценка). Уже проверенные (DONE) помечаем is_graded и переносим оценку и
    комментарий учителя в эту попытку."""
    Submission = apps.get_model('contest', 'Submission')
    Attempt = apps.get_model('contest', 'Attempt')

    for sub in Submission.objects.filter(status__in=['TESTING', 'DONE']).iterator():
        done = sub.status == 'DONE'
        if done and not sub.is_graded:
            sub.is_graded = True
            sub.save(update_fields=['is_graded'])
        if Attempt.objects.filter(submission=sub).exists():
            continue
        Attempt.objects.create(
            submission=sub,
            number=1,
            code=sub.code or '',
            submitted_at=sub.last_submitted_at or sub.updated_at,
            score=sub.score if done else None,
            teacher_comment=(sub.teacher_comment or '') if done else '',
            graded_at=sub.updated_at if done else None,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0013_attempts'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
