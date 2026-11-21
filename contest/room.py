# -*- coding: utf-8 -*-
"""«Компания Пифагор» (бывший коворкинг класса): офис, присутствие (в кеше), сообщения, поднятые руки,
задания от руководителя и зарплата.

База почти не трогается: позиции/сообщения — в кеше (FileBasedCache, общий для всех воркеров),
статистика решённых задач считается одним запросом и кешируется на 10 секунд на весь зал."""
import math
import time

from django.core.cache import cache

ONLINE = 25            # секунд без опроса — игрок ушёл из зала
BUBBLE = 10            # секунд висит облачко с сообщением
SAY_MAX = 60           # символов в сообщении
SAY_EVERY = 4          # не чаще одного сообщения раз в 4 секунды
LOG_MAX = 60           # последних сообщений хранится для учителя
TTL = 6 * 3600
PER_ROW = 12           # мест в ряду (3 блока по 4 стола)


def layout(n_students):
    """Планировка зала. Сетка W×H клеток; объекты: стены, доска, столы с компьютерами, стулья, диван, растения.
    seats — места: [{'id', 'x', 'y', 'teacher'}]. block — занятые клетки (по ним не ходят)."""
    rows = max(2, min(4, math.ceil(max(n_students, 1) / PER_ROW)))
    W = 24
    H = 7 + rows * 3 + 3
    objs, seats, block = [], [], set()

    def put(kind, x, y, w=1, d=1, solid=True, **kw):
        objs.append(dict(kw, t=kind, x=x, y=y, w=w, d=d))
        if solid:
            for i in range(w):
                for j in range(d):
                    block.add((x + i, y + j))

    # доска на задней стене и стол учителя
    put('board', 6, 0, 8, 1)
    put('tdesk', 9, 3, 2, 1)
    seats.append({'id': 't', 'x': 10, 'y': 2, 'teacher': True})
    put('chair', 10, 2, solid=False, face='down')
    put('rug', 5, 1, 10, 4, solid=False)
    # ряды столов: стол (y) и стул перед ним (y+1), ученики смотрят на доску
    sid = 0
    for r in range(rows):
        y = 7 + r * 3
        for bx in (2, 7, 12):
            for i in range(4):
                x = bx + i
                put('desk', x, y)
                seats.append({'id': f's{sid}', 'x': x, 'y': y + 1, 'teacher': False})
                put('chair', x, y + 1, solid=False, face='up')
                sid += 1
    # зона отдыха справа: диван, столик, растения, кулер, шкаф
    put('sofa', 20, 7, 1, 3)
    put('table', 18, 8)
    put('shelf', 17, 0, 4, 1)
    put('cooler', 22, 1)
    for (px, py) in ((1, 1), (22, 4), (1, H - 2), (22, H - 2), (16, 1)):
        put('plant', px, py)
    put('bean', 18, H - 3, solid=True)
    put('bean', 20, H - 3, solid=True)
    put('lrug', 17, 6, 6, 6, solid=False)
    boss = [[x, y] for x in range(8, 12) for y in (4, 5)]           # «приёмная» руководителя: сюда подходят за заданием
    return {'W': W, 'H': H, 'objs': objs, 'seats': seats, 'block': sorted(block), 'boss': boss}


# ------------------------------------------------------------------ присутствие

def _key(rid, uid):
    return f'room:{rid}:p:{uid}'


def _ids_key(rid):
    return f'room:{rid}:ids'


def people(rid, now=None):
    """Все, кто сейчас в зале: {uid: {...}}."""
    now = now or time.time()
    ids = cache.get(_ids_key(rid)) or []
    if not ids:
        return {}
    got = cache.get_many([_key(rid, u) for u in ids])
    out = {}
    for u in ids:
        p = got.get(_key(rid, u))
        if p and now - p.get('ts', 0) < ONLINE:
            out[u] = p
    return out


def touch(rid, uid, base, now=None, **changes):
    """Обновить себя в зале (опрос, ход, сообщение…). base — имя/учитель/аватар при первом входе."""
    now = now or time.time()
    k = _key(rid, uid)
    p = cache.get(k)
    fresh = p is None
    if fresh:
        base = base() if callable(base) else base         # имя/баннер/аватар считаются только при входе
        p = dict({'seat': None}, **base)
        p.update(x=base.get('x', 11), y=base.get('y', 5), msg='', mt=0, hand=False)
    elif not changes and now - p.get('ts', 0) < 8:
        return p                              # частые опросы не пишут в кеш лишний раз
    p.update(changes)
    p['ts'] = now
    cache.set(k, p, TTL)
    if fresh:
        ids = cache.get(_ids_key(rid)) or []
        if uid not in ids:
            cache.set(_ids_key(rid), ids + [uid], TTL)
    return p


def get(rid, uid):
    return cache.get(_key(rid, uid))


def seat_taken(rid, seat, uid):
    return any(p.get('seat') == seat for u, p in people(rid).items() if u != uid)


def say(rid, uid, text, now=None):
    """Сообщение над головой (и в журнал учителя). Возвращает ошибку или None."""
    now = now or time.time()
    text = ' '.join((text or '').split())[:SAY_MAX]
    if not text:
        return 'empty'
    if not cache.add(f'room:{rid}:say:{uid}', 1, SAY_EVERY):
        return 'wait'
    p = touch(rid, uid, {}, now, msg=text, mt=now)
    log = cache.get(f'room:{rid}:log') or []
    log.append([int(now), p.get('n', ''), text, bool(p.get('teacher'))])
    cache.set(f'room:{rid}:log', log[-LOG_MAX:], TTL)
    return None


def log_event(rid, name, text):
    """Событие в журнал руководителя (взял задание и т.п.)."""
    log = cache.get(f'room:{rid}:log') or []
    log.append([int(time.time()), name, text, False])
    cache.set(f'room:{rid}:log', log[-LOG_MAX:], TTL)


def log(rid):
    return cache.get(f'room:{rid}:log') or []


def public(rid, now=None):
    """Для браузера: [[uid, имя, x, y, место, сообщение, рука, учитель, цвет, html-плашка с баннером, картинка аватара]]."""
    now = now or time.time()
    out = []
    for u, p in people(rid, now).items():
        msg = p.get('msg', '') if now - p.get('mt', 0) < BUBBLE else ''
        out.append([u, p.get('n', ''), p.get('x'), p.get('y'), p.get('seat'), msg, bool(p.get('hand')),
                    bool(p.get('teacher')), p.get('c', 0), p.get('h', ''), p.get('ai', '')])
    return out


# ------------------------------------------------------------------ компания: задания и зарплата

PAY = {'A': 10000, 'B': 20000, 'C': 30000}          # ₸ за лёгкую / среднюю / сложную задачу
BOSS = {(x, y) for x in range(8, 12) for y in (4, 5)}


def near_boss(p):
    return p is not None and (round(p.get('x', -9)), round(p.get('y', -9))) in BOSS


def company(room, fresh=False):
    """Задания и зарплата по ученикам: {student_id: {'salary', 'done': [task_id], 'cur': task_id|None}}.
    Заодно отмечает выполненные задания (задача решена полностью после выдачи).
    Кешируется на 10 секунд на весь офис — 1–2 запроса к базе на всех."""
    key = f'room:{room.id}:co'
    data = None if fresh else cache.get(key)
    if data is not None:
        return data
    from django.utils import timezone
    from .models import RoomAssignment, Submission
    rows = list(RoomAssignment.objects.filter(room=room).values('id', 'student_id', 'task_id', 'pay', 'assigned_at', 'done_at'))
    open_ = [r for r in rows if r['done_at'] is None]
    if open_:
        since = min(r['assigned_at'] for r in open_)
        solved = {}
        for sid, tid, upd in (Submission.objects.filter(student_id__in={r['student_id'] for r in open_},
                                                        task_id__in={r['task_id'] for r in open_}, is_graded=True, score=10,
                                                        updated_at__gte=since).values_list('student_id', 'task_id', 'updated_at')):
            solved[(sid, tid)] = upd
        now = timezone.now()
        done_ids = [r['id'] for r in open_ if (r['student_id'], r['task_id']) in solved
                    and solved[(r['student_id'], r['task_id'])] >= r['assigned_at']]
        if done_ids:
            RoomAssignment.objects.filter(id__in=done_ids, done_at__isnull=True).update(done_at=now)
            for r in rows:
                if r['id'] in done_ids:
                    r['done_at'] = now
    data = {}
    for r in rows:
        d = data.setdefault(r['student_id'], {'salary': 0, 'done': [], 'cur': None})
        if r['done_at'] is None:
            d['cur'] = r['task_id']
        else:
            d['salary'] += r['pay']
            d['done'].append(r['task_id'])
    cache.set(key, data, 10)
    return data


def take(room, student, presence):
    """Ученик подошёл к руководителю — выдать следующее задание из списка.
    Возвращает (task, None) или (None, ошибка): far / busy / all_done."""
    from .models import RoomAssignment, Submission, Task
    if not near_boss(presence):
        return None, 'far'
    mine = company(room, fresh=True).get(student.id, {})
    if mine.get('cur'):
        return None, 'busy'
    tasks = {t.id: t for t in room.tasks.all()}
    order = [i for i in (room.task_order or []) if i in tasks] + sorted((i for i in tasks if i not in (room.task_order or [])),
                                                                        key=lambda i: (tasks[i].level, i))
    had = set(RoomAssignment.objects.filter(room=room, student=student).values_list('task_id', flat=True))
    solved = set(Submission.objects.filter(student=student, task_id__in=order, is_graded=True, score=10).values_list('task_id', flat=True))
    nxt = next((i for i in order if i not in had and i not in solved), None)     # уже решённые раньше — не выдаём
    if nxt is None:
        return None, 'all_done'
    t = tasks[nxt]
    RoomAssignment.objects.get_or_create(room=room, student=student, task=t, defaults={'pay': PAY.get(t.level, PAY['A'])})
    cache.delete(f'room:{room.id}:co')
    return t, None


def add_exit(rid, uid):
    """Ученик вышел из полного экрана или ушёл на другую вкладку — учитель видит счётчик 🚪."""
    k = f'room:{rid}:exits'
    data = cache.get(k) or {}
    data[uid] = data.get(uid, 0) + 1
    cache.set(k, data, TTL)


def exits(rid):
    return cache.get(f'room:{rid}:exits') or {}
