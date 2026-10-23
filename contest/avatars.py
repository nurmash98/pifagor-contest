"""Аватары учеников. По умолчанию — значок 🎓 (поле Student.avatar пустое).
kind: 'free' — доступен всем; дальше появятся 'gift' (праздничные подарки) и 'paid' (за монеты)."""
CATALOG = [
    dict(key='school_girl', file='avatars/school-girl.webp', kind='free', ru='Оқушы қыз', kk='Оқушы қыз'),
    dict(key='school_boy', file='avatars/school-boy.webp', kind='free', ru='Оқушы бала', kk='Оқушы бала'),
]
BY_KEY = {a['key']: a for a in CATALOG}


def available_for(student):
    """Аватары, которые ученик может выбрать (пока — все бесплатные)."""
    return [a for a in CATALOG if a['kind'] == 'free']


def get(key):
    return BY_KEY.get(key or '')
