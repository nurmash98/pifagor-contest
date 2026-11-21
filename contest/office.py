# -*- coding: utf-8 -*-
"""«Офис выходного дня»: по субботам и воскресеньям 9:00–20:00 (Алматы) открыт общий офис без учителя.

У каждой параллели свой кабинет (стеклянные стены, дверь в общий коридор) — ходить можно везде, садиться —
только в кабинете своей параллели. Ученик сам выбирает задачи (по темам) и решает их прямо в офисе:
  • за задачу, впервые решённую на 10/10, пока он в офисе, — монеты x2 (обычные + столько же сверху);
  • «зарплата»: A — 10 000 ₸, B — 20 000 ₸, C — 30 000 ₸;
  • после 20:00 топ-3 каждой параллели по зарплате за день получают по 20 монет.
Позиции и сообщения — в кеше (как в «Компании»), в базу пишется только решённое и награды."""
import math
from datetime import datetime, time as dtime

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import room as rm

OPEN_DAYS = (5, 6)                   # суббота, воскресенье
OPEN_FROM, OPEN_TO = dtime(9, 0), dtime(20, 0)
TOP_N, TOP_COINS = 3, 20
SEATS_PER_CABINET = 12
CAB_W, COL = 9, 10                   # ширина кабинета и шаг (кабинет + стеклянная стена)


def rid(day):
    return f'w{day.isoformat()}'


def is_open(now=None):
    now = timezone.localtime(now or timezone.now())
    return now.weekday() in OPEN_DAYS and OPEN_FROM <= now.time() < OPEN_TO


def closes_at(now=None):
    now = timezone.localtime(now or timezone.now())
    return timezone.make_aware(datetime.combine(now.date(), OPEN_TO))


def grades():
    """Параллели, у которых есть ученики (кроме 1–4 классов): ['5', '6', …, '11']."""
    key = 'office:grades'
    out = cache.get(key)
    if out is None:
        from .models import Student
        from .quarters import grade_of
        gs = {grade_of(c) for c in Student.objects.values_list('school_class', flat=True).distinct() if c}
        out = sorted((g for g in gs if g.isdigit() and int(g) >= 5), key=int)
        cache.set(key, out, 600)
    return out


ADMIN = 'adm'


def layout(grade_list):
    """Этаж офиса: кабинеты параллелей в два ряда, между ними коридор с лаунжем; последний кабинет —
    «Администрация» для учителей (4 рабочих стола). В кабинете параллели 2 блока по 6 мест;
    места помечены параллелью (seat['grade']), в администрации — 'adm' и teacher=True."""
    grade_list = list(grade_list) + [ADMIN]
    n = max(1, len(grade_list))
    per_row = math.ceil(n / 2)
    W = 1 + per_row * COL + 6                       # +6 — лаунж в конце коридора
    H = 19
    objs, seats, block, managers = [], [], set(), []

    def put(kind, x, y, w=1, d=1, solid=True, **kw):
        objs.append(dict(kw, t=kind, x=x, y=y, w=w, d=d))
        if solid:
            for i in range(w):
                for j in range(d):
                    block.add((x + i, y + j))

    for k, g in enumerate(grade_list):
        row, col = (0, k) if k < per_row else (1, k - per_row)
        ox = 1 + col * COL
        if row == 0:
            oy, wall_y, door_y, a = 1, 7, 7, 2          # интерьер y 1..6, стекло по y=7
        else:
            oy, wall_y, door_y, a = 12, 11, 11, 13      # стекло по y=11, интерьер y 12..17
        admin = g == ADMIN
        put('zone', ox, oy, CAB_W, 6, solid=False, c='wood' if admin else ('carpet' if k % 2 else 'wood'))
        for x in range(ox, ox + CAB_W):
            if x not in (ox + 4,):                      # дверь
                put('glass', x, wall_y, axis='x')
        for y in range(min(oy, wall_y), max(oy + 5, wall_y) + 1):
            put('glass', ox + CAB_W, y, axis='y')
        put('sign', ox + 4, door_y, 1, 1, solid=False, text='Администрация' if admin else f'{g} класс', icon='👔' if admin else '🏢')
        put('plant', ox + (CAB_W - 1 if row == 0 else 0), oy if row == 0 else oy + 5)
        if g != ADMIN:                                # руководитель кабинета стоит у входа, внутри
            managers.append({'grade': g, 'x': ox + 5, 'y': 6 if row == 0 else 12, 'name': MANAGER_NAMES[k % len(MANAGER_NAMES)]})
        sid = 0
        if admin:                                    # учительская: 4 больших стола, кресла лицом к двери
            dy = a + 1
            for k2, bx in enumerate((ox + 1, ox + 5)):
                for yy in (dy, dy + 3) if row == 0 else (dy, dy + 3):
                    cy = yy - 1 if row == 0 else yy - 1
                    put('tdesk', bx + 1, yy, 2, 1, cx=bx + 2, cy=cy)
                    seats.append({'id': f'{ADMIN}-{sid}', 'x': bx + 2, 'y': cy, 'teacher': True, 'grade': ADMIN}); sid += 1
                    put('chair', bx + 2, cy, solid=False, face='down', boss=True)
            put('shelf', ox + 3, oy if row == 0 else oy + 5, 3, 1) if row == 0 else put('plant', ox + 8, oy + 5)
            continue
        for bx in (ox + 1, ox + 5):
            for i in range(3):
                x = bx + i
                put('desk', x, a + 1, cx=x, cy=a, face='down')
                seats.append({'id': f'{g}-{sid}', 'x': x, 'y': a, 'teacher': False, 'grade': g}); sid += 1
                put('chair', x, a, solid=False, face='down')
                put('desk', x, a + 2, cx=x, cy=a + 3, face='up')
                seats.append({'id': f'{g}-{sid}', 'x': x, 'y': a + 3, 'teacher': False, 'grade': g}); sid += 1
                put('chair', x, a + 3, solid=False, face='up')
    # коридор 8..10 и лаунж в его конце
    put('zone', 0, 8, W, 3, solid=False, c='carpet')
    lx = 1 + per_row * COL
    put('zone', lx, 1, 5, 17, solid=False, c='lounge')
    put('logo', lx, 0, 5, 1)
    put('counter', lx + 1, 1, 3, 1)
    put('coffee', lx + 1, 1, solid=False)
    put('sofa', lx + 1, 4, 3, 1, face='down')
    put('table', lx + 2, 5)
    put('sofa', lx + 1, 6, 3, 1, face='up')
    put('bean', lx + 1, 13)
    put('bean', lx + 3, 13)
    put('cooler', lx + 4, 15)
    for (px, py) in ((lx + 4, 1), (lx, 17), (lx + 4, 17)):
        put('plant', px, py)
    return {'W': W, 'H': H, 'objs': objs, 'seats': seats, 'block': sorted(block), 'boss': [], 'managers': managers}


MANAGER_NAMES = ['Ерлан', 'Динара', 'Бауыржан', 'Айгерім', 'Нұрлан', 'Жанар', 'Серік', 'Мадина', 'Тимур', 'Әсел']
PACKAGE_SIZE = 3
PACKAGE_BONUS = 10000            # премия руководителя за все 3 задачи, ₸


def manager_of(grade):
    return next((m for m in layout(grades())['managers'] if m['grade'] == grade), None)


def near_manager(presence, grade):
    m = manager_of(grade)
    if not m or not presence:
        return False
    return abs(round(presence.get('x', -9)) - m['x']) <= 2 and abs(round(presence.get('y', -9)) - m['y']) <= 2


def topics_for(student):
    """Темы (теги) с нерешёнными задачами: [{'id', 'name', 'level', 'left'}] — level: где ученик остановился."""
    from .models import Submission, Task
    solved = set(Submission.objects.filter(student=student, is_graded=True, score=10).values_list('task_id', flat=True))
    by_tag = {}
    for t in Task.objects.filter(hidden=False).prefetch_related('tags').only('id', 'level'):
        for g in t.tags.all():
            by_tag.setdefault((g.id, g.name), []).append(t)
    out = []
    for (gid, name), ts in sorted(by_tag.items(), key=lambda kv: kv[0][1].lower()):
        free = [t for t in ts if t.id not in solved]
        if not free:
            continue
        level = min(t.level for t in free)               # самый лёгкий уровень, где ещё есть нерешённые: там и остановился
        out.append({'id': gid, 'name': name, 'level': level, 'left': len(free)})
    return out


def give_package(student, grade, tag_id, day):
    """Руководитель даёт 3 нерешённые задачи по теме на уровне, где ученик остановился. (pkg, ошибка)"""
    from .models import OfficePackage, Submission, Tag, Task
    if OfficePackage.objects.filter(student=student, day=day, done_at__isnull=True).exists():
        return None, 'busy'
    tag = Tag.objects.filter(id=tag_id).first()
    if tag is None:
        return None, 'bad'
    solved = set(Submission.objects.filter(student=student, is_graded=True, score=10).values_list('task_id', flat=True))
    free = sorted((t for t in Task.objects.filter(hidden=False, tags=tag).only('id', 'level') if t.id not in solved),
                  key=lambda t: (t.level, t.id))
    if not free:
        return None, 'none'
    level = free[0].level
    pick = [t.id for t in free if t.level == level][:PACKAGE_SIZE]
    if len(pick) < PACKAGE_SIZE:                         # на этом уровне меньше трёх — добираем со следующего
        pick += [t.id for t in free if t.id not in pick][:PACKAGE_SIZE - len(pick)]
    pkg = OfficePackage.objects.create(day=day, student=student, grade=grade, tag_name=tag.name, level=level, tasks=pick)
    return pkg, None


def check_packages(day, student_ids):
    """Все задачи задания решены на 10/10 — премия. Вызывается из collect() для тех, кто в офисе."""
    from django.utils import timezone as tz
    from .models import OfficePackage, Submission
    changed = False
    for pkg in OfficePackage.objects.filter(day=day, student_id__in=student_ids, done_at__isnull=True):
        ok = set(Submission.objects.filter(student_id=pkg.student_id, task_id__in=pkg.tasks, is_graded=True, score=10)
                 .values_list('task_id', flat=True))
        if set(pkg.tasks) <= ok:
            pkg.done_at, pkg.bonus = tz.now(), PACKAGE_BONUS
            pkg.save(update_fields=['done_at', 'bonus'])
            cache.delete(f'office:me:{day}:{pkg.student_id}')
            changed = True
    if changed:
        cache.delete(f'office:stats:{day}')


def seat_grade(seat_id):
    return (seat_id or '').split('-', 1)[0] if seat_id and '-' in seat_id else None


SPAWN = (3, 9)


# ------------------------------------------------------------------ решения в офисе: x2 монеты и зарплата

def collect(day, now=None):
    """Проверяет учеников, которые сейчас в офисе: задачи, впервые решённые на 10/10 после их входа, —
    засчитываются офису (x2 монеты + зарплата). Раз в 10 секунд на весь офис."""
    if not cache.add(f'office:collect:{day}', 1, 10):
        return
    from django.db.models import Min
    from .coins import LEVEL_COINS
    from .models import Attempt, CoinTransaction, OfficeDay, OfficeSolve, Submission
    now = now or timezone.now()
    online = [p for p in rm.people(rid(day)).values() if p.get('sid') and not p.get('teacher')]
    if not online:
        return
    joined = {p['sid']: p.get('j', 0) for p in online}
    check_packages(day, list(joined))
    grade = {p['sid']: p.get('g', '') for p in online}
    since = datetime.fromtimestamp(min(joined.values()), tz=timezone.get_current_timezone()) if joined else now
    subs = list(Submission.objects.filter(student_id__in=list(joined), is_graded=True, score=10, updated_at__gte=since)
                .select_related('task').exclude(task__hidden=True))
    if not subs:
        return
    have = set(OfficeSolve.objects.filter(student_id__in=list(joined), task_id__in={s.task_id for s in subs})
               .values_list('student_id', 'task_id'))
    first = dict(Attempt.objects.filter(submission__in=subs, score=10).values('submission_id')
                 .annotate(t=Min('submitted_at')).values_list('submission_id', 't'))
    for s in subs:
        if (s.student_id, s.task_id) in have:
            continue
        when = first.get(s.id) or s.updated_at
        if when.timestamp() < joined[s.student_id] - 1:          # решена раньше, чем ученик вошёл в офис
            continue
        lt = timezone.localtime(when)
        if lt.date() != day or not is_open(when):
            continue
        coins = LEVEL_COINS.get(s.task.level, 1)
        try:
            with transaction.atomic():
                OfficeDay.objects.get_or_create(day=day)
                OfficeSolve.objects.create(day=day, student_id=s.student_id, task=s.task, grade=grade.get(s.student_id, ''),
                                           money=rm.PAY.get(s.task.level, rm.PAY['A']), coins=coins, at=when)
                CoinTransaction.objects.get_or_create(student_id=s.student_id, key=f'office:{s.task_id}',
                                                      defaults=dict(kind='office', amount=coins, at=when,
                                                                    note=f'x2 в офисе: {s.task.title}'[:200]))
        except IntegrityError:
            continue
        cache.delete(f'office:me:{day}:{s.student_id}')
    cache.delete(f'office:stats:{day}')


def stats(day):
    """{grade: [[student_id, money, solved]]} за день — по убыванию зарплаты. Кеш 10 секунд."""
    key = f'office:stats:{day}'
    data = cache.get(key)
    if data is None:
        from django.db.models import Count, Max, Sum
        from .models import OfficePackage, OfficeSolve
        rows = {}
        for r in (OfficeSolve.objects.filter(day=day).values('student_id', 'grade')
                  .annotate(m=Sum('money'), n=Count('id'), last=Max('at'))):
            rows[r['student_id']] = [r['grade'], r['m'], r['n'], r['last']]
        for r in OfficePackage.objects.filter(day=day, bonus__gt=0).values('student_id', 'grade').annotate(b=Sum('bonus'), last=Max('done_at')):
            row = rows.setdefault(r['student_id'], [r['grade'], 0, 0, r['last']])
            row[1] += r['b']                             # премии руководителя входят в зарплату дня
        data = {}
        for sid, (g, m, n, last) in sorted(rows.items(), key=lambda kv: (-kv[1][1], kv[1][3])):
            data.setdefault(g, []).append([sid, m, n])
        cache.set(key, data, 10)
    return data


def finalize_due(now=None):
    """После 20:00 выдать топ-3 каждой параллели по 20 монет (один раз на день)."""
    from .models import CoinTransaction, OfficeDay
    now = timezone.localtime(now or timezone.now())
    for od in OfficeDay.objects.filter(finalized=False, day__lte=now.date()):
        if od.day == now.date() and now.time() < OPEN_TO:
            continue
        with transaction.atomic():
            od = OfficeDay.objects.select_for_update().get(id=od.id)
            if od.finalized:
                continue
            cache.delete(f'office:stats:{od.day}')
            for g, rows in stats(od.day).items():
                for place, (sid, money, n) in enumerate(rows[:TOP_N], 1):
                    CoinTransaction.objects.get_or_create(
                        student_id=sid, key=f'officetop:{od.day}',
                        defaults=dict(kind='office', amount=TOP_COINS, at=timezone.make_aware(datetime.combine(od.day, OPEN_TO)),
                                      note=f'Офис {od.day:%d.%m}: {place} место в {g} параллели'))
            od.finalized = True
            od.save(update_fields=['finalized'])
