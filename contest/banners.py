"""Баннеры под именем ученика. Сейчас один — «Флаг» (подарок ко Дню Республики):
выдаётся за 5 решённых на 10/10 задач темы «Республика күні»."""
from .models import Submission

REPUBLIC_TAG = 'Республика күні'
REPUBLIC_NEED = 5

CATALOG = {
    'kz_flag': dict(ru='Флаг Казахстана', kk='Қазақстан туы', tag=REPUBLIC_TAG, need=REPUBLIC_NEED),
}


def owned(student):
    return [k for k in (student.owned_banners or '').split(',') if k in CATALOG]


def holiday_progress(student):
    """Сколько задач темы «Республика күні» ученик решил на 10/10 (не больше нужного)."""
    if student is None:
        return 0
    return (Submission.objects.filter(student=student, is_graded=True, score=10,
                                      task__tags__name=REPUBLIC_TAG)
            .values('task_id').distinct().count())


def sync(student):
    """Выдаёт заработанные баннеры. Новый баннер сразу ставится под имя, если там пусто.
    Возвращает список ключей новых баннеров."""
    if student is None:
        return []
    have = owned(student)
    new = []
    if 'kz_flag' not in have and holiday_progress(student) >= REPUBLIC_NEED:
        new.append('kz_flag')
    if new:
        student.owned_banners = ','.join(have + new)
        fields = ['owned_banners']
        if not student.name_banner:
            student.name_banner = new[0]
            fields.append('name_banner')
        student.save(update_fields=fields)
    return new
