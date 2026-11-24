from django.db import migrations
from django.db.models import Q


def forwards(apps, schema_editor):
    """Монеты за места — только с сегодняшних уроков; старые (закрытые до 24.11.2026) не награждаем задним числом."""
    import datetime
    from django.utils import timezone
    StudyRoom = apps.get_model('contest', 'StudyRoom')
    cut = timezone.make_aware(datetime.datetime(2026, 11, 24, 0, 0))
    StudyRoom.objects.filter(Q(closed_at__lt=cut) | Q(closed_at__isnull=True, ends_at__lt=cut)).update(top_awarded=True)


class Migration(migrations.Migration):
    dependencies = [('contest', '0082_room_top_awarded')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
