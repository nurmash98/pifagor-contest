"""Монеты (тенге) учеников.

Начисления (каждое — один раз, по уникальному ключу):
  • задача решена на 10/10: уровень A — 1, B — 2, C — 3 монеты;
  • первая задача дня, решённая на 10/10: ещё +5 монет (без коэффициента уровня);
  • каждые 50 решённых задач (50, 100, 150, …): +25 монет;
  • СОР/СОЧ на 10/10 (экзамен или пересдача): +50 монет за каждый экзамен.
Тратятся монеты в магазине (баннеры под именем, позже — аватары).
sync(student) пересчитывает всё по данным и досоздаёт недостающие начисления — его можно
вызывать сколько угодно раз."""
from django.db import IntegrityError, transaction
from django.db.models import Min, Sum
from django.utils import timezone

from .models import Attempt, CoinTransaction, Student, Submission

LEVEL_COINS = {'A': 1, 'B': 2, 'C': 3}
DAILY_BONUS = 5
MILESTONE_EVERY = 50
MILESTONE_COINS = 25
EXAM_TEN_COINS = 50
DAILY_GIFT = 5          # подарок 🎁 на странице «Курсы» — раз в день


def balance(student):
    if student is None:
        return 0
    return CoinTransaction.objects.filter(student=student).aggregate(s=Sum('amount'))['s'] or 0


def _solved(student):
    """[(когда впервые 10/10, task)] по возрастанию времени."""
    subs = list(Submission.objects.filter(student=student, is_graded=True, score=10).select_related('task'))
    first = dict(Attempt.objects.filter(submission__in=subs, score=10)
                 .values('submission_id').annotate(t=Min('submitted_at')).values_list('submission_id', 't'))
    rows = [(first.get(s.id) or s.updated_at, s.task) for s in subs]
    rows.sort(key=lambda r: r[0])
    return rows


def _entries(student):
    out = []
    solved = _solved(student)
    seen_days = set()
    for n, (when, task) in enumerate(solved, 1):
        out.append(dict(key=f'task:{task.id}', kind='task', amount=LEVEL_COINS.get(task.level, 1),
                        note=task.title[:200], at=when))
        day = timezone.localtime(when).date().isoformat()
        if day not in seen_days:
            seen_days.add(day)
            out.append(dict(key=f'day:{day}', kind='day', amount=DAILY_BONUS, note=task.title[:200], at=when))
        if n % MILESTONE_EVERY == 0:
            out.append(dict(key=f'm:{n}', kind='milestone', amount=MILESTONE_COINS, note=str(n), at=when))
    from .badges import _finished_attempts
    done_exams = set()
    for when, att in _finished_attempts(student):
        if att.exam_id not in done_exams and att.grade() >= 10:
            done_exams.add(att.exam_id)
            out.append(dict(key=f'exam:{att.exam_id}', kind='exam', amount=EXAM_TEN_COINS,
                            note=att.exam.title[:200], at=when))
    return out


def sync(student):
    """Досоздаёт недостающие начисления. Возвращает сумму новых монет."""
    if student is None:
        return 0
    have = set(CoinTransaction.objects.filter(student=student).values_list('key', flat=True))
    new = [CoinTransaction(student=student, **e) for e in _entries(student) if e['key'] not in have]
    if not new:
        return 0
    CoinTransaction.objects.bulk_create(new, ignore_conflicts=True)
    return sum(t.amount for t in new)


def gift_key(now=None):
    return f'gift:{timezone.localdate(now)}'


def gift_ready(student):
    return student is not None and not CoinTransaction.objects.filter(student=student, key=gift_key()).exists()


def claim_gift(student):
    """Ежедневный подарок: +5 монет один раз в день. True — начислено, False — уже получен сегодня."""
    _, created = CoinTransaction.objects.get_or_create(
        student=student, key=gift_key(),
        defaults=dict(kind='gift', amount=DAILY_GIFT, note='Подарок дня', at=timezone.now()))
    return created


def spend(student, key, amount, note=''):
    """Списывает монеты (покупка). True — получилось; False — не хватает монет или уже куплено."""
    try:
        with transaction.atomic():
            Student.objects.select_for_update().filter(pk=student.pk).first()   # одна покупка за раз
            if CoinTransaction.objects.filter(student=student, key=key).exists():
                return False
            if balance(student) < amount:
                return False
            CoinTransaction.objects.create(student=student, key=key, kind='buy', amount=-amount,
                                           note=note[:200], at=timezone.now())
            return True
    except IntegrityError:
        return False
