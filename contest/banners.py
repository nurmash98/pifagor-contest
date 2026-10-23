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
    'oyu':    dict(kind='paid', price=150, ru='Ою-өрнек', kk='Ою-өрнек'),
    'code':   dict(kind='paid', price=200, ru='Терминал', kk='Терминал'),
    'neon':   dict(kind='paid', price=250, ru='Неон', kk='Неон'),
    'galaxy': dict(kind='paid', price=300, ru='Галактика', kk='Галактика'),
    'fire':   dict(kind='paid', price=400, ru='Огонь', kk='От'),
    'aurora': dict(kind='paid', price=500, ru='Северное сияние', kk='Солтүстік шұғыла'),
    'gold':   dict(kind='paid', price=1000, ru='Алтын', kk='Алтын'),
}
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
