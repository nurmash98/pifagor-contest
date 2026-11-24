from django.db import migrations, models


def forwards(apps, schema_editor):
    """Темы дуэлей теперь по параллелям: 5–6 и 7 классы — input/output, if/else, for; 8–11 — все темы.
    Старые настройки тем по классам сбрасываем, чтобы действовали правила параллелей."""
    SiteSettings = apps.get_model('contest', 'SiteSettings')
    Tag = apps.get_model('contest', 'Tag')
    ids = list(Tag.objects.filter(name__in=['input/output', 'if/else', 'for']).order_by('id').values_list('id', flat=True))
    SiteSettings.objects.update(duels_class_tags={}, duels_group_tags={'5-6': ids, '7': ids} if ids else {})


class Migration(migrations.Migration):
    dependencies = [('contest', '0084_coin_kind_login')]
    operations = [
        migrations.AddField(model_name='sitesettings', name='duels_group_tags',
                            field=models.JSONField(blank=True, default=dict, verbose_name='Темы дуэлей по параллелям')),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
