"""Четверти учебного года: рейтинг считается заново в каждой четверти, итоги фиксируются
в «Зале славы». Решённые задачи, монеты, значки и баннеры ученика при этом не обнуляются.

Задача засчитывается в ту четверть, когда ученик получил за неё свой лучший (итоговый) балл.
Каникулы засчитываются в следующую четверть: окна четвертей идут подряд без пропусков."""
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

YEAR = '2026-2027'
COEF = {'A': 1, 'B': 2, 'C': 3}
TOP = 10
AWARD_COINS = {1: 200, 2: 150, 3: 100}
AWARD_COINS_REST = 50        # 4–10 места


def award_for(place):
    """Монеты за место в топ-10 параллели по итогам четверти."""
    return AWARD_COINS.get(place, AWARD_COINS_REST if place <= TOP else 0)


def _dt(y, m, d, h=0, mi=0, s=0):
    return timezone.make_aware(datetime(y, m, d, h, mi, s))


# (номер, подпись дат, начало окна или None, конец окна)
QUARTERS = [
    dict(n=1, label='01.09–25.10', start=None, end=_dt(2026, 10, 25, 23, 59, 59)),
    dict(n=2, label='02.11–27.12', start=_dt(2026, 10, 26), end=_dt(2026, 12, 27, 23, 59, 59)),
    dict(n=3, label='11.01–21.03', start=_dt(2026, 12, 28), end=_dt(2027, 3, 21, 23, 59, 59)),
    dict(n=4, label='29.03–25.05', start=_dt(2027, 3, 22), end=_dt(2027, 5, 25, 23, 59, 59)),
]
BY_N = {q['n']: q for q in QUARTERS}
YEAR_WINDOW = dict(n=0, label='2026–2027', start=None, end=QUARTERS[-1]['end'])


def current(now=None):
    """Идущая четверть (окно, куда попадают новые баллы) или None — после конца учебного года."""
    now = now or timezone.now()
    for q in QUARTERS:
        if now <= q['end']:
            return q
    return None


def days_left(q, now=None):
    now = now or timezone.now()
    return max((timezone.localtime(q['end']).date() - timezone.localtime(now).date()).days, 0)


def grade_of(school_class):
    import re
    match = re.match(r'\d+', (school_class or '').strip())
    return match.group() if match else (school_class or '').strip()


CACHE_SECONDS = 90
_CACHE_VERSION_KEY = 'quarters:ver'


def invalidate():
    """Сбросить кэш баллов (после новой оценки) — следующий запрос пересчитает."""
    from django.core.cache import cache
    try:
        cache.incr(_CACHE_VERSION_KEY)
    except ValueError:
        cache.set(_CACHE_VERSION_KEY, 1, None)


def _compute(start, end):
    """{student_id: {'score', 'solved', 'graded'}} по ВСЕМ ученикам за окно."""
    from .models import Attempt, Submission
    subs = list(Submission.objects.filter(is_graded=True)
                .values('id', 'student_id', 'score', 'task__level', 'updated_at'))
    best_time = {}
    for a in (Attempt.objects.filter(submission__is_graded=True, score__isnull=False)
              .values('submission_id', 'score', 'submitted_at')):
        key = (a['submission_id'], a['score'])
        if key not in best_time or a['submitted_at'] < best_time[key]:
            best_time[key] = a['submitted_at']
    out = {}
    for s in subs:
        when = best_time.get((s['id'], s['score'])) or s['updated_at']
        if (start is not None and when < start) or (end is not None and when > end):
            continue
        row = out.setdefault(s['student_id'], {'score': 0, 'solved': 0, 'graded': 0})
        row['graded'] += 1
        row['score'] += (s['score'] or 0) * COEF.get(s['task__level'], 0)
        if s['score'] == 10:
            row['solved'] += 1
    return out


def _window_map(start, end, fresh=False):
    """Баллы всех учеников за окно — из кэша (на CACHE_SECONDS) или заново."""
    if fresh:
        return _compute(start, end)
    from django.core.cache import cache
    ver = cache.get(_CACHE_VERSION_KEY, 0)
    key = f'quarters:scores:{ver}:{start.timestamp() if start else 0}:{end.timestamp() if end else 0}'
    data = cache.get(key)
    if data is None:
        data = _compute(start, end)
        cache.set(key, data, CACHE_SECONDS)
    return data


def scores(students, start=None, end=None, fresh=False):
    """{student_id: {'score', 'solved', 'graded'}} — баллы только за задачи, лучший балл по которым
    получен в окне [start, end]. Считается один раз для всех учеников и кэшируется."""
    data = _window_map(start, end, fresh)
    empty = {'score': 0, 'solved': 0, 'graded': 0}
    return {s.id: data.get(s.id, empty) for s in students}


def excluded_ids(quarter_n):
    """Ученики, убранные из рейтинга этой четверти (0 — «за год»: убранные хоть из одной четверти)."""
    from .models import QuarterExclusion
    qs = QuarterExclusion.objects.filter(year=YEAR)
    if quarter_n:
        qs = qs.filter(quarter=quarter_n)
    return set(qs.values_list('student_id', flat=True))


def student_ranking(students, start=None, end=None, fresh=False, quarter_n=None):
    """Ученики с баллами в окне, по убыванию; у каждого calc_total_score / calc_solved_count.
    quarter_n — номер четверти (0 — год): убранные из её рейтинга не показываются."""
    sc = scores(students, start, end, fresh)
    skip = excluded_ids(quarter_n) if quarter_n is not None else set()
    rows = []
    for st in students:
        r = sc[st.id]
        if r['graded'] == 0 or st.id in skip:
            continue
        st.calc_total_score, st.calc_solved_count, st.calc_graded = r['score'], r['solved'], r['graded']
        rows.append(st)
    rows.sort(key=lambda s: (-s.calc_total_score, -s.calc_solved_count, -s.calc_graded, s.full_name))
    return rows


def class_rows(school_classes, start=None, end=None, fresh=False):
    """Рейтинг классов за окно: средний балл всех учеников класса + баллы учителя за это окно."""
    from .models import ClassBonus, Student
    rows = []
    for cls in school_classes:
        studs = list(Student.objects.filter(school_class=cls))
        sc = scores(studs, start, end, fresh)
        vals = [sc[s.id]['score'] for s in studs]
        avg = round(sum(vals) / len(vals)) if vals else 0
        bq = ClassBonus.objects.filter(school_class=cls)
        if start is not None:
            bq = bq.filter(created_at__gte=start)
        if end is not None:
            bq = bq.filter(created_at__lte=end)
        bonus = bq.aggregate(t=Sum('points'))['t'] or 0
        rows.append({'school_class': cls, 'student_count': len(vals), 'avg_score': avg, 'bonus': bonus, 'total': avg + bonus})
    rows.sort(key=lambda r: r['total'], reverse=True)
    return rows


def freeze_due(now=None):
    """Фиксирует итоги всех закончившихся и ещё не зафиксированных четвертей: топ-10 учеников и
    все классы каждой параллели; награды 1–3 местам (монеты, баннер «Чемпион четверти» за 1 место)."""
    from .models import QuarterFreeze
    now = now or timezone.now()
    done = set(QuarterFreeze.objects.filter(year=YEAR).values_list('quarter', flat=True))
    for q in QUARTERS:
        if q['end'] < now and q['n'] not in done:
            try:
                with transaction.atomic():
                    QuarterFreeze.objects.create(year=YEAR, quarter=q['n'], frozen_at=now)
                    _freeze(q)
            except IntegrityError:
                pass    # параллельный запрос уже зафиксировал


def _freeze(q):
    from . import banners as banner_module
    from .models import CoinTransaction, HallEntry, Student
    all_students = list(Student.objects.all())
    by_grade = {}
    for st in all_students:
        by_grade.setdefault(grade_of(st.school_class), []).append(st)
    entries = []
    for grade, studs in by_grade.items():
        for place, st in enumerate(student_ranking(studs, q['start'], q['end'], fresh=True, quarter_n=q['n'])[:TOP], 1):
            entries.append(HallEntry(year=YEAR, quarter=q['n'], kind='student', grade=grade, place=place, student=st,
                                     name=st.full_name, school_class=st.school_class,
                                     score=st.calc_total_score, solved=st.calc_solved_count))
            if award_for(place):
                CoinTransaction.objects.get_or_create(
                    student=st, key=f'q:{YEAR}:{q["n"]}', defaults=dict(
                        kind='award', amount=award_for(place), at=q['end'],
                        note=f'{q["n"]} четверть — {place} место ({grade} классы)'))
            if place == 1:
                banner_module.give(st, 'champion')
        classes = sorted({s.school_class for s in studs if s.school_class})
        for place, row in enumerate(class_rows(classes, q['start'], q['end'], fresh=True), 1):
            entries.append(HallEntry(year=YEAR, quarter=q['n'], kind='class', grade=grade, place=place,
                                     name=row['school_class'], school_class=row['school_class'],
                                     score=row['total'], solved=row['student_count']))
    HallEntry.objects.bulk_create(entries)
