"""Аватары учеников. По умолчанию — значок 🎓 (поле Student.avatar пустое).
kind: 'free' — доступен всем; 'paid' — покупается в магазине за монеты (price), после покупки — навсегда."""
CATALOG = [
    dict(key='school_girl', file='avatars/school-girl.webp', kind='free', ru='Оқушы қыз', kk='Оқушы қыз'),
    dict(key='school_boy', file='avatars/school-boy.webp', kind='free', ru='Оқушы бала', kk='Оқушы бала'),
    dict(key='av01', file='avatars/av-01.webp', kind='paid', price=100, ru='Мальчик в худи', kk='Худи киген бала'),
    dict(key='av02', file='avatars/av-02.webp', kind='paid', price=100, ru='Девочка в фиолетовом', kk='Күлгін киімді қыз'),
    dict(key='av03', file='avatars/av-03.webp', kind='paid', price=100, ru='Меломан', kk='Меломан'),
    dict(key='av04', file='avatars/av-04.webp', kind='paid', price=100, ru='Рыжая в очках', kk='Көзілдірікті жирен қыз'),
    dict(key='av05', file='avatars/av-05.webp', kind='paid', price=100, ru='Крутой в очках', kk='Қара көзілдірікті бала'),
    dict(key='av06', file='avatars/av-06.webp', kind='paid', price=100, ru='Лис', kk='Түлкі'),
    dict(key='av07', file='avatars/av-07.webp', kind='paid', price=100, ru='Геймерша', kk='Геймер қыз'),
    dict(key='av08', file='avatars/av-08.webp', kind='paid', price=100, ru='Кудрявый', kk='Бұйра шашты бала'),
    dict(key='av09', file='avatars/av-09.webp', kind='paid', price=100, ru='Девочка в кепке', kk='Кепкалы қыз'),
    dict(key='av10', file='avatars/av-10.webp', kind='paid', price=100, ru='Робот', kk='Робот'),
    dict(key='av11', file='avatars/av-11.webp', kind='paid', price=100, ru='Скейтер', kk='Скейтер'),
    dict(key='av12', file='avatars/av-12.webp', kind='paid', price=100, ru='Кудрявая', kk='Бұйра шашты қыз'),
    dict(key='av13', file='avatars/av-13.webp', kind='paid', price=100, ru='Отличник', kk='Үздік оқушы'),
    dict(key='av14', file='avatars/av-14.webp', kind='paid', price=100, ru='Диджей', kk='Диджей қыз'),
    dict(key='av15', file='avatars/av-15.webp', kind='paid', price=100, ru='Кот', kk='Мысық'),
    dict(key='av16', file='avatars/av-16.webp', kind='paid', price=100, ru='Розовые волосы', kk='Қызғылт шашты қыз'),
    dict(key='av17', file='avatars/av-17.webp', kind='paid', price=100, ru='Путешественник', kk='Саяхатшы'),
    dict(key='av18', file='avatars/av-18.webp', kind='paid', price=100, ru='Эльф', kk='Эльф'),
    dict(key='av19', file='avatars/av-19.webp', kind='paid', price=100, ru='Мальчик в шапке', kk='Бөрікті бала'),
    dict(key='av20', file='avatars/av-20.webp', kind='paid', price=100, ru='Девочка с хвостиком', kk='Құйрық шашты қыз'),
    dict(key='av21', file='avatars/av-21.webp', kind='paid', price=500, ru='Дракон', kk='Айдаһар'),
    dict(key='av22', file='avatars/av-22.webp', kind='paid', price=200, ru='Панда', kk='Панда'),
    dict(key='av23', file='avatars/av-23.webp', kind='paid', price=200, ru='Пингвин', kk='Пингвин'),
    dict(key='av24', file='avatars/av-24.webp', kind='paid', price=450, ru='Космонавт', kk='Ғарышкер'),
    dict(key='av25', file='avatars/av-25.webp', kind='paid', price=250, ru='Пёс в очках', kk='Көзілдірікті ит'),
    dict(key='av26', file='avatars/av-26.webp', kind='paid', price=300, ru='Динозавр', kk='Динозавр'),
    dict(key='av27', file='avatars/av-27.webp', kind='paid', price=350, ru='Аниме-геймер', kk='Аниме геймер'),
    dict(key='av28', file='avatars/av-28.webp', kind='paid', price=300, ru='Корги-пилот', kk='Ұшқыш корги'),
    dict(key='av29', file='avatars/av-29.webp', kind='paid', price=400, ru='Неоновый кот', kk='Неон мысық'),
    dict(key='nurmash', file='avatars/nurmash-agai.webp', kind='paid', price=1000, ru='Нұрмаш ағай', kk='Нұрмаш ағай'),
    dict(key='ernar', file='avatars/ernar-agai.webp', kind='paid', price=1000, ru='Ернар ағай', kk='Ернар ағай'),
    dict(key='tilek', file='avatars/tilek-agai.webp', kind='paid', price=1000, ru='Тілек ағай', kk='Тілек ағай'),
    dict(key='nurbol', file='avatars/nurbol-agai.webp', kind='paid', price=1000, ru='Нұрбол ағай', kk='Нұрбол ағай'),
]
BY_KEY = {a['key']: a for a in CATALOG}


def owned(student):
    """Ключи купленных аватаров."""
    return {k for k in (getattr(student, 'owned_avatars', '') or '').split(',') if k}


def available_for(student):
    """Аватары, которые ученик может выбрать: бесплатные и купленные."""
    mine = owned(student) if student else set()
    return [a for a in CATALOG if a['kind'] == 'free' or a['key'] in mine]


def give(student, key):
    keys = owned(student) | {key}
    student.owned_avatars = ','.join(sorted(keys))
    student.save(update_fields=['owned_avatars'])


def get(key):
    a = BY_KEY.get(key or '')
    return a if a and a['file'] else None


# «Свой аватар»: покупается один раз (токен OWN в owned_avatars), потом ученик загружает своё фото.
# Выбранный свой аватар хранится в Student.avatar как «u<id ученика>.<версия>» — версия сбрасывает кеш браузера.
OWN = 'own'
OWN_PRICE = 2500
OWN_TITLE = dict(ru='Свой аватар', kk='Өз аватарың')


def has_own(student):
    return bool(student) and OWN in owned(student)


def custom_key(student):
    """Ключ загруженного аватара ('u12.3') или '' если фото ещё не загружено."""
    ca = getattr(student, 'custom_avatar', None) if student else None
    return f'u{student.id}.{ca.version}' if ca else ''


def custom_url(key):
    """URL картинки по ключу 'u12.3' (или None, если это не свой аватар)."""
    import re
    from django.urls import reverse
    m = re.fullmatch(r'u(\d+)\.(\d+)', key or '')
    return reverse('custom_avatar_img', args=[int(m.group(1)), int(m.group(2))]) if m else None


def process_upload(f):
    """Фото -> круг 256×256 RGBA webp (байты). ValueError с понятным текстом, если файл не подходит."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
    if f.size > 6 * 1024 * 1024:
        raise ValueError('big')
    try:
        im = Image.open(f)
        im.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('bad')
    im = ImageOps.exif_transpose(im).convert('RGBA')
    w, h = im.size
    if min(w, h) < 64:
        raise ValueError('small')
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
    big = im.resize((1024, 1024), Image.LANCZOS)
    mask = Image.new('L', (1024, 1024), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 1023, 1023), fill=255)
    out = big.resize((256, 256), Image.LANCZOS)
    out.putalpha(mask.resize((256, 256), Image.LANCZOS))
    buf = BytesIO()
    out.save(buf, 'WEBP', quality=88)
    return buf.getvalue()
