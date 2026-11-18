# -*- coding: utf-8 -*-
"""Scratch-тренажёр для 1–4 классов: уровни и проверка программ.

Программа — список блоков (как в Scratch, собирается мышкой в браузере):
  {'op': 'up'|'down'|'left'|'right'}                — лабиринт, стрелки (1 класс)
  {'op': 'fwd'} {'op': 'turn_left'} {'op': 'turn_right'}  — лабиринт: «иди вперёд», «повернись»
  {'op': 'repeat', 'n': 4, 'body': [...]}          — повторить n раз
  {'op': 'until', 'body': [...]}                   — повторять, пока не дойдёшь до рыбки
  {'op': 'if', 'cond': 'ahead'|'left'|'right', 'body': [...], 'else': [...]}  — если есть путь
  {'op': 'move', 'n': 3} {'op': 'turn', 'dir': 'r'|'l', 'deg': 90} {'op': 'penup'} {'op': 'pendown'} — рисование
Проверка всегда на сервере (браузер только показывает анимацию): лабиринт — дошёл ли кот до рыбки,
рисование — совпадает ли рисунок с образцом (линии режутся на кусочки длиной 1, порядок не важен).
Звёзды: блоков не больше, чем в эталонном решении, — 3; на 1–2 больше — 2; иначе — 1."""
import math

MAX_BLOCKS = 80
MAX_STEPS = 2000
DIRS = [(0, -1), (1, 0), (0, 1), (-1, 0)]          # вверх, вправо, вниз, влево (по часовой)
ABS = {'up': 0, 'right': 1, 'down': 2, 'left': 3}

MAZE_ABS = ['up', 'down', 'left', 'right']
MAZE_TURN = ['fwd', 'turn_left', 'turn_right']
PEN = ['move', 'turn']


def R(n, *body):
    return {'op': 'repeat', 'n': n, 'body': list(body)}


def U(*body):
    return {'op': 'until', 'body': list(body)}


def IF(cond, body, other=()):
    return {'op': 'if', 'cond': cond, 'body': list(body), 'else': list(other)}


def B(op, **kw):
    return dict(op=op, **kw)


F, TL, TR = B('fwd'), B('turn_left'), B('turn_right')
UP, DN, LT, RT = B('up'), B('down'), B('left'), B('right')


def MV(n):
    return B('move', n=n)


def TU(deg, d='r'):
    return B('turn', dir=d, deg=deg)


def maze(key, ru, kk, grid, sol, blocks, start_dir=1, hint_ru='', hint_kk=''):
    return dict(key=key, kind='maze', ru=ru, kk=kk, grid=grid, solution=sol, blocks=blocks,
                start_dir=start_dir, hint_ru=hint_ru, hint_kk=hint_kk)


def pen(key, ru, kk, sol, blocks, hint_ru='', hint_kk=''):
    return dict(key=key, kind='pen', ru=ru, kk=kk, solution=sol, blocks=blocks, hint_ru=hint_ru, hint_kk=hint_kk)


H_ARROWS_RU = 'Доведи котика до рыбки. Перетащи стрелки в программу и нажми зелёный флажок.'
H_ARROWS_KK = 'Мысықты балыққа жеткіз. Бағыттарды бағдарламаға сүйреп, жасыл туды бас.'
H_TURN_RU = 'Котик смотрит туда, куда показывает стрелка. «Иди вперёд» — шаг, «повернись» — поворот на месте.'
H_TURN_KK = 'Мысық көрсеткі жаққа қарап тұр. «Алға жүр» — бір қадам, «бұрыл» — орнында бұрылу.'
H_REP_RU = 'Используй блок «повторить» — так программа будет короче.'
H_REP_KK = '«Қайтала» блогын қолдан — бағдарлама қысқа болады.'
H_IF_RU = 'Котик сам решает, куда идти: «если впереди путь — иди, иначе — повернись». Повторяй, пока не дойдёшь до рыбки.'
H_IF_KK = 'Мысық қайда жүретінін өзі шешеді: «алда жол болса — жүр, әйтпесе — бұрыл». Балыққа жеткенше қайтала.'

LEVELS = [
    # ---------------- Лабиринт: стрелки (1 класс) ----------------
    maze('m1', 'Первые шаги', 'Алғашқы қадамдар', ['#####', '#S.F#', '#####'], [RT, RT], MAZE_ABS, hint_ru=H_ARROWS_RU, hint_kk=H_ARROWS_KK),
    maze('m2', 'Наверх', 'Жоғары', ['###', '#F#', '#.#', '#.#', '#S#', '###'], [UP, UP, UP], MAZE_ABS, hint_ru=H_ARROWS_RU, hint_kk=H_ARROWS_KK),
    maze('m3', 'Поворот', 'Бұрылыс', ['######', '#F####', '#.####', '#...S#', '######'], [LT, LT, LT, UP, UP], MAZE_ABS,
         hint_ru=H_ARROWS_RU, hint_kk=H_ARROWS_KK),
    maze('m4', 'Лесенка', 'Баспалдақ', ['#######', '#S.####', '##..###', '###..F#', '#######'], [RT, DN, RT, DN, RT, RT], MAZE_ABS,
         hint_ru=H_ARROWS_RU, hint_kk=H_ARROWS_KK),
    maze('m5', 'Короткий путь', 'Қысқа жол', ['#######', '#S....#', '#.###.#', '#...#F#', '#######'], [RT, RT, RT, RT, DN, DN], MAZE_ABS,
         hint_ru='Есть два пути — найди тот, что ведёт к рыбке.', hint_kk='Екі жол бар — балыққа апаратынын тап.'),
    maze('m6', 'Змейка', 'Жылан', ['#######', '#....F#', '#.#####', '#.....#', '#####S#', '#######'],
         [UP, LT, LT, LT, LT, UP, UP, RT, RT, RT, RT], MAZE_ABS, hint_ru=H_ARROWS_RU, hint_kk=H_ARROWS_KK),
    # ---------------- Лабиринт: вперёд и повороты (2 класс) ----------------
    maze('m7', 'Вперёд!', 'Алға!', ['######', '#S..F#', '######'], [F, F, F], MAZE_TURN, hint_ru=H_TURN_RU, hint_kk=H_TURN_KK),
    maze('m8', 'Направо', 'Оңға', ['#####', '#S..#', '###.#', '###F#', '#####'], [F, F, TR, F, F], MAZE_TURN, hint_ru=H_TURN_RU, hint_kk=H_TURN_KK),
    maze('m9', 'Налево', 'Солға', ['#####', '###F#', '###.#', '#S..#', '#####'], [F, F, TL, F, F], MAZE_TURN, hint_ru=H_TURN_RU, hint_kk=H_TURN_KK),
    maze('m10', 'Извилистая тропа', 'Ирек жол', ['######', '#S.###', '##.###', '##...#', '####F#', '######'],
         [F, TR, F, F, TL, F, F, TR, F], MAZE_TURN, hint_ru=H_TURN_RU, hint_kk=H_TURN_KK),
    maze('m11', 'Смотрит вверх', 'Жоғары қарап', ['######', '#...F#', '#.####', '#S####', '######'], [F, F, TR, F, F, F], MAZE_TURN,
         start_dir=0, hint_ru='Сейчас котик смотрит вверх.', hint_kk='Қазір мысық жоғары қарап тұр.'),
    maze('m12', 'Горы', 'Таулар', ['#######', '#F#...#', '#.#.#.#', '#...#S#', '#######'],
         [F, F, TL, F, F, TL, F, F, TR, F, F, TR, F, F], MAZE_TURN, start_dir=0, hint_ru=H_TURN_RU, hint_kk=H_TURN_KK),
    # ---------------- Повторить (3 класс) ----------------
    maze('m13', 'Длинный коридор', 'Ұзын дәліз', ['###########', '#S.......F#', '###########'], [R(8, F)], MAZE_TURN + ['repeat'],
         hint_ru='8 шагов — это много блоков. Возьми «повторить» и положи внутрь «иди вперёд».',
         hint_kk='8 қадам — көп блок. «Қайтала» блогын алып, ішіне «алға жүр» сал.'),
    maze('m14', 'Ступеньки', 'Сатылар', ['#######', '#S#####', '#..####', '##..###', '###..##', '####.F#', '#######'],
         [R(4, F, TL, F, TR)], MAZE_TURN + ['repeat'], start_dir=2, hint_ru=H_REP_RU, hint_kk=H_REP_KK),
    maze('m15', 'Вокруг озера', 'Көлді айнала', ['######', '#....#', '#.##.#', '#.##.#', '#S##F#', '######'],
         [R(3, R(3, F), TR)], MAZE_TURN + ['repeat'], start_dir=0,
         hint_ru='Повтор внутри повтора: три шага и поворот — три раза.', hint_kk='Қайталаудың ішінде қайталау: үш қадам және бұрылу — үш рет.'),
    maze('m16', 'Холмы', 'Төбелер', ['#########', '#########', '#######F#', '#####...#', '###...###', '#S..#####', '#########'],
         [R(3, F, F, TL, F, TR)], MAZE_TURN + ['repeat'], hint_ru=H_REP_RU, hint_kk=H_REP_KK),
    maze('m17', 'Обход', 'Айналып өту', ['#####', '#S..#', '###.#', '#F..#', '#####'], [R(3, F, F, TR)], MAZE_TURN + ['repeat'],
         hint_ru=H_REP_RU, hint_kk=H_REP_KK),
    # ---------------- Если и пока (4 класс) ----------------
    maze('m18', 'Пока не рыбка', 'Балыққа дейін', ['#############', '#S.........F#', '#############'], [U(F)],
         MAZE_TURN + ['repeat', 'until'],
         hint_ru='«Повторять пока не рыбка» — котик идёт, пока не дойдёт, сколько бы ни было шагов.',
         hint_kk='«Балыққа дейін қайтала» — мысық жеткенше жүре береді.'),
    maze('m19', 'Умный котик', 'Ақылды мысық', ['#######', '#S....#', '#####.#', '#F....#', '#######'],
         [U(IF('ahead', [F], [TR]))], MAZE_TURN + ['until', 'if'], hint_ru=H_IF_RU, hint_kk=H_IF_KK),
    maze('m20', 'Налево по стене', 'Қабырға бойымен', ['#######', '#F....#', '#####.#', '#S....#', '#######'],
         [U(IF('ahead', [F], [TL]))], MAZE_TURN + ['until', 'if'], hint_ru=H_IF_RU, hint_kk=H_IF_KK),
    maze('m21', 'Сам найдёт дорогу', 'Жолды өзі табады', ['########', '#S...#F#', '####.#.#', '####...#', '########'],
         [U(IF('ahead', [F], [IF('left', [TL], [TR])]))], MAZE_TURN + ['until', 'if'],
         hint_ru='Если впереди стена — посмотри налево. Есть путь слева — повернись налево, иначе — направо.',
         hint_kk='Алда қабырға болса — солға қара. Сол жақта жол болса — солға, әйтпесе оңға бұрыл.'),
    # ---------------- Рисование пером ----------------
    pen('p1', 'Линия', 'Сызық', [MV(3)], PEN, hint_ru='Карандаш оставляет след. Нарисуй такую же линию, как серая.',
        hint_kk='Қарындаш із қалдырады. Сұр сызық сияқты сыз.'),
    pen('p2', 'Уголок', 'Бұрыш', [MV(3), TU(90), MV(3)], PEN, hint_ru='Поворот на 90° — это прямой угол.', hint_kk='90°-қа бұрылу — тік бұрыш.'),
    pen('p3', 'Квадрат', 'Шаршы', [MV(3), TU(90), MV(3), TU(90), MV(3), TU(90), MV(3)], PEN,
        hint_ru='У квадрата 4 одинаковые стороны.', hint_kk='Шаршының 4 бірдей қабырғасы бар.'),
    pen('p4', 'Квадрат с повтором', 'Қайталаумен шаршы', [R(4, MV(3), TU(90))], PEN + ['repeat'],
        hint_ru='Сторона и поворот — 4 раза. Сделай это блоком «повторить».', hint_kk='Қабырға мен бұрылыс — 4 рет. «Қайтала» блогымен жаса.'),
    pen('p5', 'Прямоугольник', 'Тіктөртбұрыш', [R(2, MV(4), TU(90), MV(2), TU(90))], PEN + ['repeat'],
        hint_ru='Длинная сторона, короткая сторона — и ещё раз.', hint_kk='Ұзын қабырға, қысқа қабырға — тағы бір рет.'),
    pen('p6', 'Лестница', 'Саты', [R(3, MV(1), TU(90, 'l'), MV(1), TU(90, 'r'))], PEN + ['repeat'],
        hint_ru='Шаг вперёд, поворот налево, шаг вверх, поворот направо.', hint_kk='Алға қадам, солға бұрыл, жоғары қадам, оңға бұрыл.'),
    pen('p7', 'Треугольник', 'Үшбұрыш', [R(3, MV(3), TU(120))], PEN + ['repeat'],
        hint_ru='У треугольника 3 стороны. Поворачивай на 120°.', hint_kk='Үшбұрыштың 3 қабырғасы бар. 120°-қа бұрыл.'),
    pen('p8', 'Шестиугольник', 'Алтыбұрыш', [R(6, MV(2), TU(60))], PEN + ['repeat'],
        hint_ru='6 сторон, поворот на 60°.', hint_kk='6 қабырға, 60°-қа бұрылу.'),
    pen('p9', 'Два квадрата', 'Екі шаршы', [R(2, R(4, MV(2), TU(90)), MV(2))], PEN + ['repeat'],
        hint_ru='Нарисуй квадрат, сдвинься и нарисуй ещё один.', hint_kk='Шаршы сыз, жылжы да тағы біреуін сыз.'),
    pen('p10', 'Пунктир', 'Үзік сызық', [R(4, MV(1), B('penup'), MV(1), B('pendown'))], PEN + ['repeat', 'penup', 'pendown'],
        hint_ru='«Подними карандаш» — идёшь без следа, «опусти» — снова рисуешь.', hint_kk='«Қарындашты көтер» — ізсіз жүресің, «түсір» — қайта сызасың.'),
    pen('p11', 'Звезда', 'Жұлдыз', [R(5, MV(4), TU(144))], PEN + ['repeat'],
        hint_ru='5 лучей, поворот на 144°.', hint_kk='5 сәуле, 144°-қа бұрылу.'),
    pen('p12', 'Восьмиугольник', 'Сегізбұрыш', [R(8, MV(2), TU(45))], PEN + ['repeat'],
        hint_ru='8 сторон. На сколько градусов поворачивать? 360 : 8.', hint_kk='8 қабырға. Неше градусқа бұрылу керек? 360 : 8.'),
    pen('p13', 'Плюс', 'Қосу белгісі', [R(4, MV(2), TU(180), MV(2), TU(90))], PEN + ['repeat'],
        hint_ru='Сходи вперёд и вернись назад (поворот на 180°), потом повернись на 90°.',
        hint_kk='Алға барып, кері қайт (180°-қа бұрыл), содан кейін 90°-қа бұрыл.'),
    pen('p14', 'Цветок', 'Гүл', [R(6, R(4, MV(2), TU(90)), TU(60))], PEN + ['repeat'],
        hint_ru='Шесть квадратов, после каждого — поворот на 60°.', hint_kk='Алты шаршы, әрқайсысынан кейін 60°-қа бұрылу.'),
]
BY_KEY = {lv['key']: lv for lv in LEVELS}
TRACKS = [
    ('maze', '🐱 Лабиринт', '🐱 Лабиринт', [lv['key'] for lv in LEVELS if lv['kind'] == 'maze']),
    ('pen', '✏️ Рисование', '✏️ Сурет салу', [lv['key'] for lv in LEVELS if lv['kind'] == 'pen']),
]
MOVE_N = [1, 2, 3, 4, 5]
REPEAT_N = [2, 3, 4, 5, 6, 7, 8, 9, 10]
DEGREES = [30, 45, 60, 72, 90, 120, 144, 180]
CONDS = ['ahead', 'left', 'right']


def count_blocks(prog):
    n = 0
    for b in prog or []:
        n += 1 + count_blocks(b.get('body')) + count_blocks(b.get('else'))
    return n


def ops_used(prog):
    out = set()
    for b in prog or []:
        out.add(b.get('op'))
        out |= ops_used(b.get('body')) | ops_used(b.get('else'))
    return out


def clean(prog, allowed, depth=0):
    """Проверка и нормализация программы из браузера. None — если что-то не так."""
    if not isinstance(prog, list) or depth > 5:
        return None
    out = []
    for b in prog:
        if not isinstance(b, dict) or b.get('op') not in allowed:
            return None
        op = b['op']
        c = {'op': op}
        try:
            if op == 'repeat':
                c['n'] = int(b.get('n'))
                if c['n'] not in REPEAT_N:
                    return None
            if op == 'move':
                c['n'] = int(b.get('n'))
                if c['n'] not in MOVE_N:
                    return None
            if op == 'turn':
                c['deg'] = int(b.get('deg'))
                c['dir'] = 'l' if b.get('dir') == 'l' else 'r'
                if c['deg'] not in DEGREES:
                    return None
            if op == 'if':
                c['cond'] = b.get('cond') if b.get('cond') in CONDS else 'ahead'
        except (TypeError, ValueError):
            return None
        if op in ('repeat', 'until', 'if'):
            c['body'] = clean(b.get('body') or [], allowed, depth + 1)
            if c['body'] is None:
                return None
        if op == 'if':
            c['else'] = clean(b.get('else') or [], allowed, depth + 1)
            if c['else'] is None:
                return None
        out.append(c)
    return out


# ------------------------------------------------------------------ лабиринт
class _Stop(Exception):
    pass


def _find(grid, ch):
    for y, row in enumerate(grid):
        x = row.find(ch)
        if x >= 0:
            return x, y
    return None


def run_maze(level, prog):
    """{'ok': bool, 'reason': 'goal'|'wall'|'short'|'steps'}"""
    grid = level['grid']
    x, y = _find(grid, 'S')
    goal = _find(grid, 'F')
    st = {'x': x, 'y': y, 'd': level.get('start_dir', 1), 'steps': 0}

    def free(cx, cy):
        return 0 <= cy < len(grid) and 0 <= cx < len(grid[cy]) and grid[cy][cx] != '#'

    def tick():
        st['steps'] += 1
        if st['steps'] > MAX_STEPS:
            raise _Stop('steps')

    def go(d):
        dx, dy = DIRS[d]
        nx, ny = st['x'] + dx, st['y'] + dy
        if not free(nx, ny):
            raise _Stop('wall')
        st['x'], st['y'] = nx, ny
        if (nx, ny) == goal:
            raise _Stop('goal')

    def cond(c):
        d = {'ahead': st['d'], 'left': (st['d'] + 3) % 4, 'right': (st['d'] + 1) % 4}[c]
        dx, dy = DIRS[d]
        return free(st['x'] + dx, st['y'] + dy)

    def run(blocks):
        for b in blocks:
            tick()
            op = b['op']
            if op in ABS:
                st['d'] = ABS[op]
                go(st['d'])
            elif op == 'fwd':
                go(st['d'])
            elif op == 'turn_left':
                st['d'] = (st['d'] + 3) % 4
            elif op == 'turn_right':
                st['d'] = (st['d'] + 1) % 4
            elif op == 'repeat':
                for _ in range(b['n']):
                    run(b['body'])
            elif op == 'until':
                while True:
                    tick()
                    run(b['body'])
            elif op == 'if':
                run(b['body'] if cond(b['cond']) else b['else'])
    try:
        run(prog)
    except _Stop as e:
        return {'ok': str(e) == 'goal', 'reason': str(e)}
    return {'ok': False, 'reason': 'short'}


# ------------------------------------------------------------------ рисование
def draw_pen(prog):
    """Рисунок программы: множество отрезков длиной 1 (концы округлены). None — если программа бесконечная."""
    st = {'x': 0.0, 'y': 0.0, 'h': 0.0, 'down': True, 'steps': 0}
    pieces = set()

    def key(px, py):
        return (round(px, 2) + 0.0, round(py, 2) + 0.0)

    def run(blocks):
        for b in blocks:
            st['steps'] += 1
            if st['steps'] > MAX_STEPS:
                raise _Stop('steps')
            op = b['op']
            if op == 'move':
                for _ in range(b['n']):
                    nx = st['x'] + math.cos(math.radians(st['h']))
                    ny = st['y'] + math.sin(math.radians(st['h']))
                    if st['down']:
                        pieces.add(tuple(sorted([key(st['x'], st['y']), key(nx, ny)])))
                    st['x'], st['y'] = nx, ny
            elif op == 'turn':
                st['h'] = (st['h'] + (b['deg'] if b['dir'] == 'r' else -b['deg'])) % 360
            elif op == 'penup':
                st['down'] = False
            elif op == 'pendown':
                st['down'] = True
            elif op == 'repeat':
                for _ in range(b['n']):
                    run(b['body'])
    try:
        run(prog)
    except _Stop:
        return None
    return pieces


def target(level):
    if level['kind'] == 'pen':
        return sorted(draw_pen(level['solution']))
    return None


def check(level, prog):
    """{'ok', 'reason', 'stars', 'blocks', 'best'}"""
    best = count_blocks(level['solution'])
    used = count_blocks(prog)
    if not prog:
        return {'ok': False, 'reason': 'empty', 'stars': 0, 'blocks': 0, 'best': best}
    if level['kind'] == 'maze':
        res = run_maze(level, prog)
    else:
        drawn = draw_pen(prog)
        want = draw_pen(level['solution'])
        if drawn is None:
            res = {'ok': False, 'reason': 'steps'}
        elif drawn == want:
            res = {'ok': True, 'reason': 'same'}
        else:
            res = {'ok': False, 'reason': 'extra' if drawn - want else 'missing'}
    stars = 0
    if res['ok']:
        stars = 3 if used <= best else (2 if used <= best + 2 else 1)
    return dict(res, stars=stars, blocks=used, best=best)


def public(level, lang='ru'):
    """Что нужно браузеру для уровня (без эталонного решения)."""
    out = {'key': level['key'], 'kind': level['kind'], 'title': level['kk' if lang == 'kk' else 'ru'],
           'hint': level['hint_kk' if lang == 'kk' else 'hint_ru'] or '', 'blocks': level['blocks'],
           'best': count_blocks(level['solution']), 'move_n': MOVE_N, 'repeat_n': REPEAT_N, 'degrees': DEGREES}
    if level['kind'] == 'maze':
        out.update(grid=level['grid'], start_dir=level.get('start_dir', 1))
    else:
        out['target'] = [[list(a), list(b)] for a, b in target(level)]
    return out


def is_junior(student):
    """Ученик 1–4 класса: Scratch вместо задач на Python."""
    if student is None:
        return False
    from .quarters import grade_of
    g = grade_of(student.school_class)
    return g.isdigit() and 1 <= int(g) <= 4
