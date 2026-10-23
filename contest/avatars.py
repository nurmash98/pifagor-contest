"""Аватары учеников. По умолчанию — значок 🎓 (поле Student.avatar пустое).
kind: 'free' — доступен всем; 'soon' — скоро появится в магазине (пока выбрать нельзя)."""
CATALOG = [
    dict(key='school_girl', file='avatars/school-girl.webp', kind='free', ru='Оқушы қыз', kk='Оқушы қыз'),
    dict(key='school_boy', file='avatars/school-boy.webp', kind='free', ru='Оқушы бала', kk='Оқушы бала'),
    dict(key='zhigit', file='avatars/zhigit.webp', kind='soon', ru='Жігіт', kk='Жігіт'),
    dict(key='qyz', file='avatars/qyz.webp', kind='soon', ru='Қыз', kk='Қыз'),
    dict(key='dombyrashy', file='', kind='soon', ru='Домбырашы', kk='Домбырашы'),
    dict(key='burkitshi', file='', kind='soon', ru='Бүркітші', kk='Бүркітші'),
    dict(key='batyr', file='', kind='soon', ru='Батыр', kk='Батыр'),
    dict(key='ata', file='', kind='soon', ru='Ата', kk='Ата'),
    dict(key='altyn_adam', file='', kind='soon', ru='Алтын адам', kk='Алтын адам'),
]
BY_KEY = {a['key']: a for a in CATALOG}


def available_for(student):
    """Аватары, которые ученик может выбрать (пока — все бесплатные)."""
    return [a for a in CATALOG if a['kind'] == 'free']


def get(key):
    a = BY_KEY.get(key or '')
    return a if a and a['file'] else None
