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


BOSS = {(x, y) for x in range(2, 6) for y in (4, 5)}          # приёмная в кабинете руководителя: сюда подходят за заданием
TEACHER_SPOT = (4, 2)                                            # кресло руководителя
SPAWN_Y = 7                                                      # коридор между кабинетами и open space
ASSISTANTS = [(9, 'Аружан'), (18, 'Данияр')]                       # x стойки ассистента в коридоре (y = 7)


def layout(n_students):
    """Офис компании «Пифагор»: стеклянный кабинет руководителя (CEO), переговорная, лаунж с кофе-пойнтом
    и open space — блоки рабочих мест по 6 (столы друг напротив друга, между ними перегородка).
    seats — места: [{'id', 'x', 'y', 'teacher'}]. block — занятые клетки (по ним не ходят)."""
    per_row = 30                                     # 5 блоков × 6 мест
    rows = max(1, min(3, math.ceil(max(n_students, 1) / per_row)))
    W = 28
    H = 9 + rows * 5 + 1
    objs, seats, block = [], [], set()

    def put(kind, x, y, w=1, d=1, solid=True, **kw):
        objs.append(dict(kw, t=kind, x=x, y=y, w=w, d=d))
        if solid:
            for i in range(w):
                for j in range(d):
                    block.add((x + i, y + j))

    def glass(x, y, axis):
        put('glass', x, y, axis=axis)

    # --- кабинет руководителя (CEO): x 1..7, y 1..5; стекло по y=6 (дверь x=4..5) и по x=8
    put('zone', 1, 1, 7, 5, solid=False, c='wood')
    for x in (1, 2, 3, 6, 7):
        glass(x, 6, 'x')
    for y in range(1, 7):
        glass(8, y, 'y')
    put('sign', 4, 6, 2, 1, solid=False, text='CEO')
    put('tdesk', 3, 3, 2, 1)
    seats.append({'id': 't', 'x': TEACHER_SPOT[0], 'y': TEACHER_SPOT[1], 'teacher': True})
    put('chair', TEACHER_SPOT[0], TEACHER_SPOT[1], solid=False, face='down', boss=True)
    put('shelf', 1, 0, 3, 1)
    put('plant', 7, 1)
    put('plant', 1, 5)
    put('armchair', 6, 4)
    # --- лаунж и кофе-пойнт: x 9..18, y 1..5; логотип компании на задней стене
    put('logo', 10, 0, 7, 1)
    put('zone', 9, 1, 10, 5, solid=False, c='lounge')
    put('sofa', 10, 2, 3, 1, face='down')
    put('table', 11, 3)
    put('sofa', 10, 4, 3, 1, face='up')
    put('counter', 15, 1, 3, 1)
    put('coffee', 15, 1, solid=False)
    put('cooler', 18, 1)
    put('bean', 15, 4)
    put('bean', 17, 4)
    put('plant', 9, 1)
    # --- переговорная: x 20..26, y 1..6; стекло по y=7 (дверь x=23) и по x=19
    put('zone', 20, 1, 7, 6, solid=False, c='wood')
    for x in (20, 21, 22, 24, 25, 26):
        glass(x, 7, 'x')
    for y in range(1, 8):
        glass(19, y, 'y')
    put('tv', 21, 0, 5, 1)
    put('mtable', 21, 3, 5, 2)
    for x in range(21, 26):
        put('mchair', x, 2, face='down')
        put('mchair', x, 5, face='up')
    put('plant', 26, 1)
    # --- open space: блоки по 6 мест (3 стола сверху, 3 снизу, лицом друг к другу)
    put('zone', 1, 9, W - 2, rows * 5, solid=False, c='carpet')
    sid = 0
    for r in range(rows):
        y0 = 9 + r * 5                                # стулья верхнего ряда
        for bx in (2, 7, 12, 17, 22):
            for i in range(3):
                x = bx + i
                put('desk', x, y0 + 1, cx=x, cy=y0, face='down')       # стул сверху, смотрит вниз на стол
                seats.append({'id': f's{sid}', 'x': x, 'y': y0, 'teacher': False}); sid += 1
                put('chair', x, y0, solid=False, face='down')
                put('desk', x, y0 + 2, cx=x, cy=y0 + 3, face='up')     # стул снизу, смотрит вверх
                seats.append({'id': f's{sid}', 'x': x, 'y': y0 + 3, 'teacher': False}); sid += 1
                put('chair', x, y0 + 3, solid=False, face='up')
    for (px, py) in ((1, H - 2), (W - 2, H - 2), (1, 8), (W - 2, 8)):
        put('plant', px, py)
    # ассистенты руководителя — стойки в коридоре: выдают задания и подсказывают (руководитель отдыхает)
    for i, (ax, name) in enumerate(ASSISTANTS):
        put('help', ax - 1, 7)
    return {'W': W, 'H': H, 'objs': objs, 'seats': seats, 'block': sorted(block), 'boss': [],
            'assistants': [{'x': ax, 'y': 7, 'name': name} for ax, name in ASSISTANTS]}


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
    # Проверяем при каждой записи, а не только при первом входе: список мог потеряться
    # (одновременная запись из разных воркеров, очистка кеша) — тогда ученик «исчезал» из зала навсегда.
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
    """Для браузера: [[uid, имя, x, y, место, сообщение, рука, учитель, цвет, html-плашка с баннером, картинка аватара, ағай]]."""
    now = now or time.time()
    out = []
    for u, p in people(rid, now).items():
        msg = p.get('msg', '') if now - p.get('mt', 0) < BUBBLE else ''
        out.append([u, p.get('n', ''), p.get('x'), p.get('y'), p.get('seat'), msg, bool(p.get('hand')),
                    bool(p.get('teacher')), p.get('c', 0), p.get('h', ''), p.get('ai', ''), p.get('ag', '')])
    return out


# ------------------------------------------------------------------ компания: задания и зарплата

PAY = {'A': 10000, 'B': 20000, 'C': 30000}
SWAP_MAX = 2          # сколько раз за урок ученик может поменять задание
TOP_N = 10   # после рабочего дня места по зарплате: 1 место — 10 монет, 2 — 9, … 10 — 1          # ₸ за лёгкую / среднюю / сложную задачу


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
    rows = list(RoomAssignment.objects.filter(room=room, skipped_at__isnull=True)
                .values('id', 'student_id', 'task_id', 'pay', 'assigned_at', 'done_at'))
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
    from .models import RoomFine
    data = {}
    for f in RoomFine.objects.filter(room=room).values('student_id', 'amount', 'reason'):   # штрафы руководителя
        d = data.setdefault(f['student_id'], {'salary': 0, 'done': [], 'cur': None})
        d['salary'] -= f['amount']
        d['fines'] = d.get('fines', 0) + f['amount']
        d['fine_last'] = [f['amount'], f['reason']]
    for r in rows:
        d = data.setdefault(r['student_id'], {'salary': 0, 'done': [], 'cur': None})
        if r['done_at'] is None:
            d['cur'] = r['task_id']
        else:
            d['salary'] += r['pay']
            d['done'].append(r['task_id'])
    cache.set(key, data, 10)
    return data


def take(room, student, presence, avoid=None):
    """Ученик подошёл к ассистенту — выдать следующее задание из списка.
    Отложенные (поменянные) задания идут в конец очереди. avoid — не выдавать эту задачу.
    Возвращает (task, None) или (None, ошибка): busy / all_done."""
    from django.utils import timezone
    from .models import RoomAssignment, Submission
    mine = company(room, fresh=True).get(student.id, {})
    if mine.get('cur'):
        return None, 'busy'
    tasks = {t.id: t for t in room.tasks.all()}
    order = [i for i in (room.task_order or []) if i in tasks] + sorted((i for i in tasks if i not in (room.task_order or [])),
                                                                        key=lambda i: (tasks[i].level, i))
    mine_rows = {r.task_id: r for r in RoomAssignment.objects.filter(room=room, student=student)}
    skipped = sorted((r for r in mine_rows.values() if r.skipped_at), key=lambda r: r.skipped_at)
    solved = set(Submission.objects.filter(student=student, task_id__in=order, is_graded=True, score=10).values_list('task_id', flat=True))
    queue = [i for i in order if i not in mine_rows and i not in solved] + \
            [r.task_id for r in skipped if r.task_id not in solved]          # уже решённые раньше — не выдаём
    nxt = next((i for i in queue if i != avoid), None)
    if nxt is None:
        return None, 'all_done'
    t = tasks[nxt]
    row = mine_rows.get(nxt)
    if row is not None:                       # отложенное раньше — выдаём заново
        RoomAssignment.objects.filter(pk=row.pk).update(skipped_at=None, assigned_at=timezone.now())
    else:
        RoomAssignment.objects.get_or_create(room=room, student=student, task=t, defaults={'pay': PAY.get(t.level, PAY['A'])})
    cache.delete(f'room:{room.id}:co')
    return t, None


def swap(room, student):
    """Ученик меняет текущее задание: оно уходит в конец очереди, сразу выдаётся следующее.
    Не больше SWAP_MAX раз за урок. Возвращает (task, None) или (None, ошибка):
    no_task / swap_limit / no_other."""
    from django.db.models import Sum
    from django.utils import timezone
    from .models import RoomAssignment
    cur = company(room, fresh=True).get(student.id, {}).get('cur')
    if not cur:
        return None, 'no_task'
    used = RoomAssignment.objects.filter(room=room, student=student).aggregate(n=Sum('skips'))['n'] or 0
    if used >= SWAP_MAX:
        return None, 'swap_limit'
    row = RoomAssignment.objects.get(room=room, student=student, task_id=cur)
    RoomAssignment.objects.filter(pk=row.pk).update(skipped_at=timezone.now(), skips=row.skips + 1)
    cache.delete(f'room:{room.id}:co')
    task, err = take(room, student, None, avoid=cur)
    if err:                                   # других задач нет — возвращаем как было
        RoomAssignment.objects.filter(pk=row.pk).update(skipped_at=None, skips=row.skips)
        cache.delete(f'room:{room.id}:co')
        return None, 'no_other'
    return task, None


def add_exit(rid, uid):
    """Ученик вышел из полного экрана или ушёл на другую вкладку — учитель видит счётчик 🚪."""
    k = f'room:{rid}:exits'
    data = cache.get(k) or {}
    data[uid] = data.get(uid, 0) + 1
    cache.set(k, data, TTL)


def exits(rid):
    return cache.get(f'room:{rid}:exits') or {}


# ------------------------------------------------------------------ личные сообщения (нажал на человека — написал ему)

DM_MAX = 200          # символов
DM_KEEP = 40          # сообщений в переписке
DM_EVERY = 2          # не чаще раза в 2 секунды


def _pair(rid, a, b):
    a, b = sorted((int(a), int(b)))
    return f'room:{rid}:dm:{a}-{b}'


def dm_send(rid, frm, to, text, absent_ok=False):
    """Ошибка или None. Писать можно только тому, кто сейчас в зале/офисе
    (absent_ok — в офисе: вопрос своему преподавателю / его ответ, даже если адресата сейчас нет)."""
    text = ' '.join((text or '').split())[:DM_MAX]
    if not text:
        return 'empty'
    if int(to) == int(frm) or (not absent_ok and int(to) not in people(rid)):
        return 'gone'
    if not cache.add(f'room:{rid}:dmsay:{frm}', 1, DM_EVERY):
        return 'wait'
    k = _pair(rid, frm, to)
    conv = cache.get(k) or []
    conv.append([int(frm), text, int(time.time())])
    cache.set(k, conv[-DM_KEEP:], TTL)
    uk = f'room:{rid}:unread:{int(to)}'
    unread = cache.get(uk) or {}
    unread[str(int(frm))] = unread.get(str(int(frm)), 0) + 1
    cache.set(uk, unread, TTL)
    return None


def dm_get(rid, me, other):
    """Переписка с other; заодно отмечает её прочитанной."""
    uk = f'room:{rid}:unread:{int(me)}'
    unread = cache.get(uk) or {}
    if unread.pop(str(int(other)), None):
        cache.set(uk, unread, TTL)
    return cache.get(_pair(rid, me, other)) or []


def dm_unread(rid, me):
    return cache.get(f'room:{rid}:unread:{int(me)}') or {}


# ------------------------------------------------------------------ подсказки ассистента (без ИИ)

TAG_TIPS = {
    'for': 'Подумайте, сколько раз нужно повторить действие, и запишите это в range(...). Конец в range не входит.',
    'while': 'Цикл while повторяет, пока условие верно. Не забудьте менять переменную внутри цикла — иначе он не закончится.',
    'if/else': 'Выпишите все случаи из условия и для каждого — свою ветку if / elif / else. Проверьте граничные значения.',
    'string': 'Строку удобно перебирать: for ch in s. Длина — len(s), символ по номеру — s[i] (нумерация с 0).',
    'list': 'Список читают так: a = list(map(int, input().split())). Индексы идут с 0, последний — a[-1].',
    'list-methods': 'Вспомните методы списка: append, insert, pop, remove, sort, count, index.',
    'input/output': 'Каждый input() читает одну строку. Числа переводите через int(...). Выводите ровно то, что просят.',
    'indexing': 'Индексы начинаются с 0; отрицательные считают с конца: s[-1] — последний.',
}
ERROR_TIPS = [
    ('timeout', 'Программа работает слишком долго — возможно, бесконечный цикл или слишком много повторений.'),
    ('memory', 'Программа использует слишком много памяти.'),
    ('compile', 'Код не компилируется — проверьте ошибки компиляции.'),
    ('SyntaxError', 'Синтаксическая ошибка: проверьте двоеточия, скобки и кавычки.'),
    ('IndentationError', 'Ошибка отступов: тело цикла/условия должно быть сдвинуто на 4 пробела.'),
    ('NameError', 'Используется переменная или функция, которой нет (опечатка в имени?).'),
    ('ValueError', 'Неверное преобразование: например int() от строки с пробелами — проверьте, как читаете ввод.'),
    ('ZeroDivisionError', 'Деление на ноль — проверьте, может ли делитель быть 0.'),
    ('IndexError', 'Выход за границы: индекс больше длины строки/списка.'),
    ('TypeError', 'Смешаны разные типы, например число и строка — переведите через int() или str().'),
    ('EOFError', 'Программа читает больше строк ввода, чем дано.'),
]


def hint(task, student, lang='ru'):
    """Подсказка по заданию: совет по теме, пример из условия, на каком тесте падает последнее решение."""
    from .models import Submission
    own = (task.hint_kk if lang == 'kk' and task.hint_kk else task.hint).strip()   # подсказка, которую учитель написал к задаче
    tip = next((TAG_TIPS[g.name] for g in task.tags.all() if g.name in TAG_TIPS), None)
    sub = (Submission.objects.filter(student=student, task=task).exclude(autotest_total=None)
           .order_by('-updated_at').only('autotest_results', 'autotest_passed', 'autotest_total').first())
    fail = None
    if sub is not None:
        for n, r in enumerate(sub.autotest_results or [], 1):
            if not r.get('ok'):
                err = str(r.get('error') or '')
                why = next((t for key, t in ERROR_TIPS if key in err), None) or (
                    'Неверный ответ на этом тесте — сравните свой вывод с условием.' if not err else err[:160])
                fail = {'n': n, 'input': str(r.get('input') or '')[:200], 'why': why,
                        'passed': sub.autotest_passed, 'total': sub.autotest_total}
                break
        if fail is None:
            fail = {'n': 0, 'passed': sub.autotest_passed, 'total': sub.autotest_total}
    if lang == 'kk':
        from .templatetags.ui_i18n import UI_TRANSLATIONS as TR
        tip = TR.get(tip, tip) if tip else tip
        if fail and fail.get('why'):
            fail['why'] = TR.get(fail['why'], fail['why'])
    if own:
        tip = own
    return {'tip': tip, 'ex_in': task.get_display_input_example(lang)[:300],
            'ex_out': (task.output_example_kk if lang == 'kk' and task.output_example_kk else task.output_example)[:300],
            'fail': fail}


def award_top(room):
    """Рабочий день окончен: места по зарплате (с учётом штрафов, только с зарплатой > 0):
    1 место — 10 монет, 2 — 9, … 10 место — 1.
    Один раз на урок (StudyRoom.top_awarded). Возвращает [(место, student_id)]."""
    from django.db import transaction
    from django.utils import timezone
    from .models import CoinTransaction, StudyRoom
    if room.top_awarded or room.is_open():
        return []
    with transaction.atomic():
        r = StudyRoom.objects.select_for_update().get(pk=room.pk)
        if r.top_awarded:
            return []
        co = company(r, fresh=True)
        ranked = sorted(((d.get('salary', 0), len(d.get('done', [])), sid) for sid, d in co.items()), reverse=True)
        winners = [(i, sid) for i, (sal, _n, sid) in enumerate([x for x in ranked if x[0] > 0][:TOP_N], 1)]
        when = r.closed_at or r.ends_at or timezone.now()
        for place, sid in winners:
            CoinTransaction.objects.get_or_create(
                student_id=sid, key=f'roomtop:{r.id}',
                defaults=dict(kind='game', amount=TOP_N + 1 - place, at=when,
                              note=f'Компания «Пифагор» {r.school_class}: {place} место'))
        r.top_awarded = True
        r.save(update_fields=['top_awarded'])
    room.top_awarded = True
    return winners
