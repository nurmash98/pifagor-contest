from django.db import migrations


class Migration(migrations.Migration):
    """Раньше загружала «Подготовку к районным олимпиадам»; раздел убран (см. 0078) — теперь ничего не делает."""
    dependencies = [('contest', '0076_exam_practice')]
    operations = []
