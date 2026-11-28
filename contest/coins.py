"""Монеты (тенге) учеников.

Начисления (каждое — один раз, по уникальному ключу):
  • задача решена на 10/10: уровень A — 1, B — 2, C — 3 монеты;
  • первая задача дня, решённая на 10/10: ещё +5 монет (без коэффициента уровня);
  • каждые 50 решённых задач (50, 100, 150, …): +25 монет;
  • каждые 5 задач, решённых за один день (5, 10, 15 …): +5 монет (с 28.11.2026);
  • огонёк 🔥 — решать хотя бы одну новую задачу каждый день подряд: 3 дня +3, 7 дней +10, 14 дней +15,
    30 дней +30, 50 дней +50, 100 дней +100, 200 дней +200, 300 → +300 и т.д.; пропустил день — огонёк гаснет и считается заново
    (призы — с 28.11.2026);
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
FIVE_EVERY, FIVE_COINS = 5, 5                    # каждые 5 задач за день — +5 монет
FIVE_FROM = '2026-11-28'                          # раньше этого дня не начисляем (без «подарка» за прошлое)
STREAK_PRIZES = {3: 3, 7: 10, 14: 15, 30: 30, 50: 50}   # дней подряд → монет; дальше 100 → 100, 200 → 200, 300 → 300 …


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
    seen_days, per_day, first_at = set(), {}, {}
    for n, (when, task) in enumerate(solved, 1):
        out.append(dict(key=f'task:{task.id}', kind='task', amount=LEVEL_COINS.get(task.level, 1),
                        note=task.title[:200], at=when))
        day = timezone.localtime(when).date().isoformat()
        per_day[day] = per_day.get(day, 0) + 1
        first_at.setdefault(day, when)
        if day >= FIVE_FROM and per_day[day] % FIVE_EVERY == 0:
            out.append(dict(key=f'five:{day}:{per_day[day]}', kind='five', amount=FIVE_COINS,
                            note=f'{per_day[day]} задач за день', at=when))
        if day not in seen_days:
            seen_days.add(day)
            out.append(dict(key=f'day:{day}', kind='day', amount=DAILY_BONUS, note=task.title[:200], at=when))
        if n % MILESTONE_EVERY == 0:
            out.append(dict(key=f'm:{n}', kind='milestone', amount=MILESTONE_COINS, note=str(n), at=when))
    # огонёк: дни подряд, в которые решена хотя бы одна новая задача
    from datetime import date, timedelta
    run, prev = 0, None
    for day in sorted(seen_days):
        d = date.fromisoformat(day)
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        prev = d
        prize = streak_prize(run)
        if prize and day >= FIVE_FROM:
            out.append(dict(key=f'fire:{day}', kind='streak', amount=prize, note=f'🔥 Огонёк: {run} дней подряд',
                            at=first_at[day]))
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


def solve_days(student_id):
    """Дни (date), в которые ученик впервые решил какую-нибудь задачу на 10/10. Кешируется на минуту."""
    from datetime import date
    from django.core.cache import cache
    ck = f'solvedays:{student_id}'
    got = cache.get(ck)
    if got is None:
        st = Student.objects.filter(id=student_id).first()
        got = sorted({timezone.localtime(w).date().isoformat() for w, _t in _solved(st)}) if st else []
        cache.set(ck, got, 60)
    return {date.fromisoformat(x) for x in got}


def streak(student_id, today=None):
    """Огонёк: сколько дней подряд ученик решал хотя бы одну новую задачу.
    Если сегодня ещё не решал — считаем до вчера (огонёк ещё горит, но погаснет в полночь)."""
    from datetime import timedelta
    today = today or timezone.localdate()
    days = solve_days(student_id)
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n, today in days


def streak_prize(n):
    return STREAK_PRIZES.get(n) or (n if n >= 100 and n % 100 == 0 else 0)
