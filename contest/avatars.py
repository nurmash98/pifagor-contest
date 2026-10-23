"""Аватары учеников. По умолчанию — значок 🎓 (поле Student.avatar пустое).
kind: 'free' — доступен всем; 'soon' — скоро появится в магазине (пока выбрать нельзя)."""
CATALOG = [
    dict(key='school_girl', file='avatars/school-girl.webp', kind='free', ru='Оқушы қыз', kk='Оқушы қыз'),
    dict(key='school_boy', file='avatars/school-boy.webp', kind='free', ru='Оқушы бала', kk='Оқушы бала'),
    dict(key='soon1', file='', kind='soon', ru='', kk=''),
    dict(key='soon2', file='', kind='soon', ru='', kk=''),
    dict(key='soon3', file='', kind='soon', ru='', kk=''),
    dict(key='soon4', file='', kind='soon', ru='', kk=''),
    dict(key='soon5', file='', kind='soon', ru='', kk=''),
    dict(key='soon6', file='', kind='soon', ru='', kk=''),
    dict(key='soon7', file='', kind='soon', ru='', kk=''),
]
BY_KEY = {a['key']: a for a in CATALOG}


def available_for(student):
    """Аватары, которые ученик может выбрать (пока — все бесплатные)."""
    return [a for a in CATALOG if a['kind'] == 'free']


def get(key):
    a = BY_KEY.get(key or '')
    return a if a and a['file'] else None
