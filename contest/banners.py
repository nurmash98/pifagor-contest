"""Баннеры под именем ученика.
  • free — у всех сразу;
  • gift — «Флаг Казахстана»: за 5 задач темы «Республика күні» на 10/10;
           «Vice City»: челлендж GTA VI — 6 из 10 задач на 10/10 (и выбор одного аватара-персонажа);
  • paid — покупаются за монеты в магазине."""
from .models import Submission

REPUBLIC_TAG = 'Республика күні'
REPUBLIC_NEED = 5

# Челленджи: решил need задач темы tag на 10/10 — получаешь баннер и аватар (страница /challenge/<ключ>/)
GTA_TAG = 'GTA VI'
CHALLENGES = {
    # pick — аватары на выбор: ученик берёт только один (на странице челленджа)
    'gta6': dict(tag=GTA_TAG, need=6, banner='vice', pick=[f'gta{i:02d}' for i in range(1, 12)], ru='Челлендж GTA VI', kk='GTA VI челленджі'),
}

# порядок = порядок в магазине
CATALOG = {
    'plain':  dict(kind='free', ru='Простой', kk='Қарапайым'),
    'mint':   dict(kind='free', ru='Мятный', kk='Жалбыз'),
    'sky':    dict(kind='free', ru='Небо', kk='Аспан'),
    'champion': dict(kind='award', ru='Чемпион четверти', kk='Тоқсан чемпионы'),
    'kz_flag': dict(kind='gift', ru='Флаг Казахстана', kk='Қазақстан туы', tag=REPUBLIC_TAG, need=REPUBLIC_NEED),
    'vice': dict(kind='gift', ru='Vice City', kk='Vice City', tag=GTA_TAG, need=6, challenge='gta6'),
    'oyu':    dict(kind='paid', price=300, ru='Ою-өрнек', kk='Ою-өрнек'),
    'code':   dict(kind='paid', price=400, ru='Терминал', kk='Терминал'),
    'chess': dict(kind='paid', price=400, ru='Шахматы', kk='Шахмат'),
    'python': dict(kind='paid', price=400, ru='Python', kk='Python'),
    'pixel': dict(kind='paid', price=450, ru='Пиксель', kk='Пиксель'),
    'ocean': dict(kind='paid', price=450, ru='Океан', kk='Мұхит'),
    'neon':   dict(kind='paid', price=500, ru='Неон', kk='Неон'),
    'rainbow': dict(kind='paid', price=500, ru='Радуга', kk='Кемпірқосақ'),
    'sakura': dict(kind='paid', price=550, ru='Сакура', kk='Сакура'),
    'matrix': dict(kind='paid', price=600, ru='Матрица', kk='Матрица'),
    'ice': dict(kind='paid', price=600, ru='Лёд', kk='Мұз'),
    'galaxy': dict(kind='paid', price=700, ru='Галактика', kk='Галактика'),
    'steppe': dict(kind='paid', price=700, ru='Дала', kk='Дала'),
    'cyber': dict(kind='paid', price=800, ru='Киберпанк', kk='Киберпанк'),
    'storm': dict(kind='paid', price=800, ru='Молния', kk='Найзағай'),
    'fire':   dict(kind='paid', price=900, ru='Огонь', kk='От'),
    'shanyrak': dict(kind='paid', price=900, ru='Шаңырақ', kk='Шаңырақ'),
    'rocket': dict(kind='paid', price=1000, ru='Ракета', kk='Зымыран'),
    'aurora': dict(kind='paid', price=1200, ru='Северное сияние', kk='Солтүстік шұғыла'),
    'diamond': dict(kind='paid', price=1500, ru='Бриллиант', kk='Гауһар'),
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


def catalog():
    """Каталог баннеров с правками админа (цена, названия, скрыт)."""
    from . import shop_items
    return {k: shop_items.merged(b, 'banner', k) for k, b in CATALOG.items()}


def owned(student):
    """Баннеры ученика: бесплатные + полученные/купленные."""
    extra = [k for k in (getattr(student, 'owned_banners', '') or '').split(',') if k in CATALOG]
    return FREE + [k for k in extra if k not in FREE]


def give(student, key):
    have = [k for k in (student.owned_banners or '').split(',') if k]
    if key not in have:
        student.owned_banners = ','.join(have + [key])
        student.save(update_fields=['owned_banners'])


def progress(student, tag):
    """Сколько задач темы tag ученик решил на 10/10."""
    if student is None:
        return 0
    return (Submission.objects.filter(student=student, is_graded=True, score=10, task__tags__name=tag)
            .values('task_id').distinct().count())


def holiday_progress(student):
    """Сколько задач темы «Республика күні» ученик решил на 10/10."""
    return progress(student, REPUBLIC_TAG)


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
    for ch in CHALLENGES.values():
        if ch['banner'] in owned(student) or progress(student, ch['tag']) < ch['need']:
            continue
        give(student, ch['banner'])
        new.append(ch['banner'])
        if not student.name_banner:
            student.name_banner = ch['banner']
            student.save(update_fields=['name_banner'])
    return new


def picked(student, key):
    """Какой аватар ученик уже выбрал в челлендже (или None)."""
    from . import avatars
    mine = avatars.owned(student) if student else set()
    return next((k for k in CHALLENGES[key]['pick'] if k in mine), None)


def pick(student, key, avatar_key):
    """Выбор одного аватара за челлендж. True — выдан."""
    from . import avatars
    ch = CHALLENGES.get(key)
    if ch is None or avatar_key not in ch['pick'] or picked(student, key):
        return False
    if progress(student, ch['tag']) < ch['need']:
        return False
    avatars.give(student, avatar_key)
    student.avatar = avatar_key
    student.save(update_fields=['avatar'])
    return True
