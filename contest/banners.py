"""Баннеры под именем ученика.
  • free — у всех сразу;
  • gift — «Флаг Казахстана»: за 5 задач темы «Республика күні» на 10/10;
  • paid — покупаются за монеты в магазине."""
from .models import Submission

REPUBLIC_TAG = 'Республика күні'
REPUBLIC_NEED = 5

# порядок = порядок в магазине
CATALOG = {
    'plain':  dict(kind='free', ru='Простой', kk='Қарапайым'),
    'mint':   dict(kind='free', ru='Мятный', kk='Жалбыз'),
    'sky':    dict(kind='free', ru='Небо', kk='Аспан'),
    'kz_flag': dict(kind='gift', ru='Флаг Казахстана', kk='Қазақстан туы', tag=REPUBLIC_TAG, need=REPUBLIC_NEED),
    'oyu':    dict(kind='paid', price=300, ru='Ою-өрнек', kk='Ою-өрнек'),
    'code':   dict(kind='paid', price=400, ru='Терминал', kk='Терминал'),
    'neon':   dict(kind='paid', price=500, ru='Неон', kk='Неон'),
    'galaxy': dict(kind='paid', price=700, ru='Галактика', kk='Галактика'),
    'fire':   dict(kind='paid', price=900, ru='Огонь', kk='От'),
    'aurora': dict(kind='paid', price=1200, ru='Северное сияние', kk='Солтүстік шұғыла'),
    'gold':   dict(kind='paid', price=2000, ru='Алтын', kk='Алтын'),
    'custom': dict(kind='paid', price=5000, ru='Свой баннер', kk='Өз баннерің'),
}

# «Свой баннер»: цвета выбирает ученик, значок и эффект — из готовых списков
CUSTOM_ICONS = ['', '⭐', '🔥', '💎', '👑', '🚀', '⚡', '🏆', '🎯', '🧠', '💻', '🐍', '🦅', '🐺', '🌙', '☀️', '🌸', '🍀', '🎵', '⚽', '🇰🇿']
CUSTOM_FX = ['none', 'shine', 'pulse', 'flow']
CUSTOM_DEFAULT = {'c1': '#1a1a4b', 'c2': '#008b42', 'fg': '#ffffff', 'icon': '⭐', 'fx': 'shine'}


def _hex(value, fallback):
    import re
    return value if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) else fallback


def clean_custom(data):
    """Проверенные настройки «своего баннера» (всё лишнее отбрасывается)."""
    data = data if isinstance(data, dict) else {}
    d = CUSTOM_DEFAULT
    return {'c1': _hex(data.get('c1'), d['c1']), 'c2': _hex(data.get('c2'), d['c2']), 'fg': _hex(data.get('fg'), d['fg']),
            'icon': data.get('icon') if data.get('icon') in CUSTOM_ICONS else d['icon'],
            'fx': data.get('fx') if data.get('fx') in CUSTOM_FX else d['fx']}


def custom_style(data):
    c = clean_custom(data)
    return f"--cb1:{c['c1']};--cb2:{c['c2']};--cbfg:{c['fg']}"

FREE = [k for k, b in CATALOG.items() if b['kind'] == 'free']


def owned(student):
    """Баннеры ученика: бесплатные + полученные/купленные."""
    extra = [k for k in (getattr(student, 'owned_banners', '') or '').split(',') if k in CATALOG]
    return FREE + [k for k in extra if k not in FREE]


def give(student, key):
    have = [k for k in (student.owned_banners or '').split(',') if k]
    if key not in have:
        student.owned_banners = ','.join(have + [key])
        student.save(update_fields=['owned_banners'])


def holiday_progress(student):
    """Сколько задач темы «Республика күні» ученик решил на 10/10."""
    if student is None:
        return 0
    return (Submission.objects.filter(student=student, is_graded=True, score=10,
                                      task__tags__name=REPUBLIC_TAG)
            .values('task_id').distinct().count())


def sync(student):
    """Выдаёт заработанные баннеры. Новый подарок сразу ставится под имя, если там пусто.
    Возвращает список ключей новых баннеров."""
    if student is None:
        return []
    new = []
    if 'kz_flag' not in owned(student) and holiday_progress(student) >= REPUBLIC_NEED:
        give(student, 'kz_flag')
        new.append('kz_flag')
        if not student.name_banner:
            student.name_banner = 'kz_flag'
            student.save(update_fields=['name_banner'])
    return new
