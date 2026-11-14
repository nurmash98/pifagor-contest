# -*- coding: utf-8 -*-
"""Игра «Битва за остров». Класс стоит на общей карте из клеток: чтобы шагнуть на соседнюю клетку,
нужно решить её задачу, и клетка красится в цвет команды. Трава — «что выведет код» (1 очко),
лес и крепость — написать программу (2 и 3 очка), чужая клетка — на уровень сложнее.
Игра идёт в полноэкранном режиме: MAX_EXITS выходов — ученика выбивает (клетки остаются команде)."""
import math
import random
from collections import deque
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import codegame as cg
from .autotest import outputs_match, run_autotests

WATER, GRASS, FOREST, FORT = '~', '.', 'f', 'F'
POINTS = {GRASS: 1, FOREST: 2, FORT: 3}
MAX_EXITS = 3                 # столько выходов из полного экрана — и ученик выбит
COOLDOWN = 30                 # секунд паузы после неверного ответа
PRIZES = [3, 2, 1]            # монеты за 1, 2 и 3 место
MAX_TEAMS = 6
TEAM_NAMES = ['Красные', 'Синие', 'Розовые', 'Жёлтые', 'Фиолетовые', 'Оранжевые']
TEAM_NAMES_KK = ['Қызылдар', 'Көктер', 'Қызғылттар', 'Сарылар', 'Күлгіндер', 'Қызғылт сарылар']
TEAM_COLORS = ['#ef4444', '#3b82f6', '#ec4899', '#facc15', '#8b5cf6', '#f97316']   # без зелёного: карта сама зелёная
LEVELS = {2: 'A', 3: 'B', 4: 'C'}       # уровень задачи на код по сложности клетки


# ------------------------------------------------------------------ карта
def _noise(w, h, rng, step):
    """Гладкий шум 0..1: случайные значения в узлах редкой сетки + билинейная интерполяция."""
    gw, gh = w // step + 2, h // step + 2
    g = [[rng.random() for _ in range(gw)] for _ in range(gh)]
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            fx, fy = x / step, y / step
            x0, y0 = int(fx), int(fy)
            tx, ty = fx - x0, fy - y0
            top = g[y0][x0] * (1 - tx) + g[y0][x0 + 1] * tx
            bot = g[y0 + 1][x0] * (1 - tx) + g[y0 + 1][x0 + 1] * tx
            row.append(top * (1 - ty) + bot * ty)
        out.append(row)
    return out


def _components(cells):
    """Связные области (по 4 соседям) множества клеток."""
    left, out = set(cells), []
    while left:
        start = left.pop()
        comp, q = {start}, deque([start])
        while q:
            x, y = q.popleft()
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in left:
                    left.discard(n)
                    comp.add(n)
                    q.append(n)
        out.append(comp)
    return out


def generate_map(players, rng=None):
    """Остров под число игроков: примерно 14 клеток суши на человека. Вся суша связна."""
    rng = rng or random.Random()
    total = max(players, 4) * 26 + 120
    w = max(14, math.ceil(math.sqrt(total * 4 / 3)))
    h = max(11, math.ceil(total / w))
    for _ in range(20):
        shape, lakes, woods = _noise(w, h, rng, 4), _noise(w, h, rng, 3), _noise(w, h, rng, 3)
        cx, cy = (w - 1) / 2, (h - 1) / 2
        land = set()
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                d = math.hypot((x - cx) / cx, (y - cy) / cy)
                if d + (shape[y][x] - .5) * .5 < .93 and not (lakes[y][x] > .8 and d > .25):
                    land.add((x, y))
        if not land:
            continue
        land = max(_components(land), key=len)
        if len(land) >= max(players, 4) * 9:
            break
    rows = [[WATER] * w for _ in range(h)]
    for x, y in land:
        rows[y][x] = FOREST if woods[y][x] > .6 else GRASS
    # крепости: одна ближе к центру, остальные — подальше друг от друга
    forts, want = [], max(3, players // 4 + 2)
    order = sorted(land, key=lambda c: math.hypot(c[0] - cx, c[1] - cy) + rng.random() * 2)
    for c in order[:1] + rng.sample(order, len(order)):
        if len(forts) >= want:
            break
        if all(abs(c[0] - f[0]) + abs(c[1] - f[1]) >= 4 for f in forts):
            forts.append(c)
            rows[c[1]][c[0]] = FORT
    return {'w': w, 'h': h, 'rows': [''.join(r) for r in rows]}


def terrain(game, x, y):
    m = game.map or {}
    if not (0 <= x < m.get('w', 0) and 0 <= y < m.get('h', 0)):
        return WATER
    return m['rows'][y][x]


def _land(game):
    m = game.map
    return {(x, y) for y in range(m['h']) for x in range(m['w']) if m['rows'][y][x] != WATER}


def _start_zones(game, sizes, rng, taken=()):
    """Для каждой команды — её стартовые клетки. Центры зон выбираются как можно дальше друг от друга."""
    land = _land(game)
    free = [c for c in land if terrain(game, *c) != FORT and c not in taken]
    seeds = []
    busy = set(taken)
    zones = []
    for size in sizes:
        pool = [c for c in free if c not in busy]
        if not pool:
            zones.append([])
            continue
        if not seeds and not taken:
            seed = rng.choice(pool)
        else:
            anchors = seeds + list(taken)
            seed = max(pool, key=lambda c: (min(abs(c[0] - a[0]) + abs(c[1] - a[1]) for a in anchors), rng.random()))
        seeds.append(seed)
        zone, q, seen = [], deque([seed]), {seed}
        while q and len(zone) < size:
            c = q.popleft()
            if c not in busy and terrain(game, *c) != FORT:
                zone.append(c)
            x, y = c
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in land and n not in seen:
                    seen.add(n)
                    q.append(n)
        busy.update(zone)
        zones.append(zone)
    return zones


# ------------------------------------------------------------------ команды
def team_count(game, players):
    if game.mode == 'solo':
        return len(players)
    return max(1, min(game.teams_count, MAX_TEAMS, len(players)))


def team_color(i, total):
    if total <= len(TEAM_COLORS):
        return TEAM_COLORS[i % len(TEAM_COLORS)]
    return 'hsl(%d, 78%%, %d%%)' % (round(i * 360 / total), 46 if i % 2 else 58)


def teams_info(game, players, lang='ru'):
    """[{i, name, color, size}] — в режиме «сам за себя» команда = ученик."""
    ids = sorted({p.team for p in players if p.team >= 0})
    total = max(len(ids), 1) if game.mode == 'solo' else max(ids[-1] + 1 if ids else 1, 1)
    names = TEAM_NAMES_KK if lang == 'kk' else TEAM_NAMES
    out = []
    for i in ids:
        members = [p for p in players if p.team == i]
        name = members[0].student.full_name if game.mode == 'solo' else names[i % len(names)]
        out.append({'i': i, 'name': name, 'color': team_color(i, total), 'size': len(members)})
    return out


def shuffle_teams(game, rng=None):
    """Случайно и поровну раскидывает игроков лобби по командам."""
    from .models import IslandPlayer
    rng = rng or random.Random()
    players = list(game.players.select_related('student'))
    rng.shuffle(players)
    k = team_count(game, players)
    for i, p in enumerate(players):
        p.team = i if game.mode == 'solo' else i % k
    IslandPlayer.objects.bulk_update(players, ['team'])
    _bump(game)
    return players


def _bump(game):
    from .models import IslandGame
    from django.db.models import F
    IslandGame.objects.filter(pk=game.pk).update(version=F('version') + 1)
    game.refresh_from_db(fields=['version'])


def set_team(game, player, team):
    if game.status != 'lobby' or game.mode == 'solo':
        return False
    player.team = max(0, min(int(team), min(game.teams_count, MAX_TEAMS) - 1))
    player.save(update_fields=['team'])
    _bump(game)
    return True


# ------------------------------------------------------------------ старт, вход, финиш
def start(game, rng=None):
    """Учитель нажал «Начать»: команды (кому не досталось — в самую маленькую), карта, стартовые клетки."""
    from .models import IslandCell, IslandGame, IslandPlayer
    rng = rng or random.Random()
    with transaction.atomic():
        game = IslandGame.objects.select_for_update().get(pk=game.pk)
        if game.status != 'lobby':
            return game
        players = list(game.players.select_related('student'))
        if not players:
            return game
        k = team_count(game, players)
        if game.mode == 'solo':
            for i, p in enumerate(players):
                p.team = i
        else:
            sizes = [0] * k
            for p in players:
                if 0 <= p.team < k:
                    sizes[p.team] += 1
                else:
                    p.team = -1
            rest = [p for p in players if p.team < 0]
            rng.shuffle(rest)                               # кого не раскидали — случайно в самые маленькие команды
            for p in rest:
                p.team = sizes.index(min(sizes))
                sizes[p.team] += 1
        game.map = generate_map(len(players), rng)
        teams = sorted({p.team for p in players})
        zones = _start_zones(game, [sum(1 for p in players if p.team == t) for t in teams], rng)
        scores, cells = {}, []
        rows = [list(r) for r in game.map['rows']]
        for zone in zones:                                  # стартовые клетки — всегда трава: у всех поровну очков
            for x, y in zone:
                rows[y][x] = GRASS
        game.map['rows'] = [''.join(r) for r in rows]
        for t, zone in zip(teams, zones):
            members = [p for p in players if p.team == t]
            for p, (x, y) in zip(members, zone):
                p.x, p.y = x, y
                cells.append(IslandCell(game=game, x=x, y=y, team=t, by=p, ver=1))
            for p in members[len(zone):]:               # на всякий случай: клеток не хватило — встают рядом
                p.x, p.y = zone[0] if zone else (0, 0)
            scores[str(t)] = sum(POINTS[terrain(game, x, y)] for x, y in zone)
        IslandPlayer.objects.bulk_update(players, ['team', 'x', 'y'])
        IslandCell.objects.bulk_create(cells)
        now = timezone.now()
        game.scores, game.version, game.status = scores, 1, 'running'
        game.started_at, game.ends_at = now, now + timedelta(minutes=game.minutes)
        game.save()
    return game


def join(game, student, rng=None):
    """Ученик входит в игру. В лобби — просто встаёт в список; если игра уже идёт — в самую маленькую команду."""
    from .models import IslandCell, IslandGame, IslandPlayer
    player = IslandPlayer.objects.filter(game=game, student=student).first()
    if player or game.status == 'finished':
        return player
    rng = rng or random.Random()
    with transaction.atomic():
        game = IslandGame.objects.select_for_update().get(pk=game.pk)
        player, created = IslandPlayer.objects.get_or_create(game=game, student=student)
        if not created:
            return player
        game.version += 1
        if game.status == 'running':
            others = list(game.players.exclude(pk=player.pk))
            owned = {(c.x, c.y): c.team for c in game.cells.all()}
            if game.mode == 'solo' or not others:
                player.team = max([p.team for p in others] + [-1]) + 1
                zone = _start_zones(game, [1], rng, taken=tuple(owned))[0]
                if zone:
                    player.x, player.y = zone[0]
                    rows = [list(r) for r in game.map['rows']]
                    rows[player.y][player.x] = GRASS
                    game.map['rows'] = [''.join(r) for r in rows]
                    IslandCell.objects.create(game=game, x=player.x, y=player.y, team=player.team, by=player, ver=game.version)
                    game.scores[str(player.team)] = POINTS[terrain(game, player.x, player.y)]
            else:
                sizes = {}
                for p in others:
                    sizes[p.team] = sizes.get(p.team, 0) + 1
                player.team = min(sizes, key=lambda t: (sizes[t], t))
                mine = [c for c, t in owned.items() if t == player.team]
                mate = next((p for p in others if p.team == player.team), None)
                player.x, player.y = rng.choice(mine) if mine else ((mate.x, mate.y) if mate else (0, 0))
            player.save()
        game.save(update_fields=['version', 'scores', 'map'])
    return player


def ranking(game):
    """[(место, команда, очки)] — равные очки делят место."""
    rows = sorted(((int(t), s) for t, s in (game.scores or {}).items()), key=lambda r: (-r[1], r[0]))
    out, place, prev = [], 0, None
    for i, (t, s) in enumerate(rows):
        if s != prev:
            place, prev = i + 1, s
        out.append((place, t, s))
    return out


def finish_if_needed(game, now=None, force=False):
    """Время вышло (или учитель завершил): игра закрывается, призёрам — монеты. Вызывать можно сколько угодно раз."""
    from .models import CoinTransaction, IslandGame
    now = now or timezone.now()
    if game.status != 'running' or not (force or (game.ends_at and now >= game.ends_at)):
        return game
    with transaction.atomic():
        game = IslandGame.objects.select_for_update().get(pk=game.pk)
        if game.status != 'running':
            return game
        game.status, game.finished_at = 'finished', now
        game.version += 1
        game.save(update_fields=['status', 'finished_at', 'version'])
        prize = {t: PRIZES[place - 1] for place, t, s in ranking(game) if place <= len(PRIZES) and s > 0}
        for p in game.players.select_related('student'):
            coins = prize.get(p.team, 0)
            if coins and not p.kicked and p.captured:
                tx, created = CoinTransaction.objects.get_or_create(
                    student=p.student, key=f'island:{game.id}',
                    defaults=dict(kind='game', amount=coins, at=now, note='Битва за остров'))
                if created:
                    p.coins = coins
                    p.save(update_fields=['coins'])
    return game


def seconds_left(game, now=None):
    if game.status != 'running' or not game.ends_at:
        return 0
    return max(0, int((game.ends_at - (now or timezone.now())).total_seconds()))


# ------------------------------------------------------------------ ход
def _owner(game, x, y):
    c = game.cells.filter(x=x, y=y).first()
    return c.team if c else None


def cell_level(game, player, x, y, owner):
    """1 — «что выведет код», 2–4 — задача на код уровня A/B/C. Чужая клетка — на уровень сложнее."""
    return POINTS[terrain(game, x, y)] + (1 if owner is not None and owner != player.team else 0)


def _pick_task(game, player, level, rng):
    from .models import Task
    qs = Task.objects.filter(hidden=False).exclude(test_cases=[])
    tag_ids = list(game.tags.values_list('id', flat=True))
    if tag_ids:
        qs = qs.filter(tags__in=tag_ids).distinct()
    want = LEVELS[level]
    for letters in ([want], ['A', 'B', 'C']):               # нет задач нужного уровня — берём любой
        ids = list(qs.filter(level__in=letters).values_list('id', flat=True))
        fresh = [i for i in ids if i not in (player.used_tasks or [])]
        if fresh or ids:
            return rng.choice(fresh or ids)
    return None


def _output_question(game, rng):
    topics = set(game.topic_list()) or {t for t, _ in cg.TOPICS}
    pool = [t for t in cg.bank() if set(t['tags']) & topics] or list(cg.bank())
    rng.shuffle(pool)
    for template in pool[:12]:
        q = cg.make_question(template, rng)
        if q:
            return q
    return None


def cooldown_left(player, now=None):
    if not player.cooldown_until:
        return 0
    return max(0, math.ceil((player.cooldown_until - (now or timezone.now())).total_seconds()))


def public_challenge(player, lang='ru'):
    """То, что видит ученик: без ответа."""
    from .models import Task
    ch = player.challenge or {}
    if not ch:
        return None
    out = {'x': ch['x'], 'y': ch['y'], 'kind': ch['kind'], 'points': ch['points'], 'enemy': ch.get('enemy', False)}
    if ch['kind'] == 'output':
        out.update(title=ch.get('title', ''), code=ch['code'])
    else:
        task = Task.objects.filter(pk=ch.get('task')).first()
        if task is None:
            return None
        out.update(title=task.get_display_title(lang), text=task.get_display_description(lang),
                   input=task.get_display_input_example(lang).replace('\\n', '\n'),
                   output=task.get_display_output_example(lang).replace('\\n', '\n'), level=task.level)
    return out


def move(game, player, x, y, rng=None, now=None):
    """Шаг на соседнюю клетку. Своя клетка — просто идём; иначе выдаём задачу этой клетки."""
    now = now or timezone.now()
    rng = rng or random.Random()
    if game.status != 'running' or seconds_left(game, now) <= 0:
        return {'error': 'over'}
    if player.kicked:
        return {'error': 'kicked'}
    if abs(x - player.x) + abs(y - player.y) != 1 or terrain(game, x, y) == WATER:
        return {'error': 'far'}
    owner = _owner(game, x, y)
    if owner == player.team:
        player.x, player.y, player.challenge = x, y, {}
        player.save(update_fields=['x', 'y', 'challenge'])
        return {'moved': True}
    wait = cooldown_left(player, now)
    if wait:
        return {'error': 'cooldown', 'wait': wait}
    level = cell_level(game, player, x, y, owner)
    ch = player.challenge or {}
    if not (ch.get('kind') == 'code' and (ch.get('x'), ch.get('y')) == (x, y) and ch.get('level') == level):
        base = {'x': x, 'y': y, 'points': POINTS[terrain(game, x, y)], 'level': level, 'enemy': owner is not None}
        task_id = _pick_task(game, player, level, rng) if level > 1 else None
        if task_id:
            ch = dict(base, kind='code', task=task_id)
        else:                                               # трава или в банке нет задач на код
            q = _output_question(game, rng)
            if not q:
                return {'error': 'noquestion'}
            ch = dict(base, kind='output', title=q['title'], code=q['code'], answer=q['answer'])
        player.challenge = ch
        player.save(update_fields=['challenge'])
    return {'challenge': True}


def answer(game, player, value, now=None):
    """Ответ на задачу клетки: верно — клетка захвачена и ученик на ней; неверно — пауза COOLDOWN секунд."""
    now = now or timezone.now()
    ch = player.challenge or {}
    if game.status != 'running' or seconds_left(game, now) <= 0:
        return {'error': 'over'}
    if player.kicked:
        return {'error': 'kicked'}
    if not ch:
        return {'error': 'nochallenge'}
    wait = cooldown_left(player, now)
    if wait:
        return {'error': 'cooldown', 'wait': wait}
    info = {}
    if ch['kind'] == 'output':
        ok = outputs_match(value or '', ch['answer'])
    else:
        from .models import Task
        task = Task.objects.filter(pk=ch.get('task')).first()
        res = run_autotests(task, value or '', 'python') if task else {'available': False}
        if not res.get('available'):
            return {'error': 'unavailable'}
        ok = res['total'] > 0 and res['passed'] == res['total']
        info = {'passed': res['passed'], 'total': res['total']}
    if not ok:
        player.cooldown_until = now + timedelta(seconds=COOLDOWN)
        fields = ['cooldown_until']
        if ch['kind'] == 'output':                          # новые числа в следующий раз
            player.challenge = {}
            fields.append('challenge')
        player.save(update_fields=fields)
        return dict(info, ok=False, wait=COOLDOWN)
    capture(game, player, ch)
    return dict(info, ok=True, points=ch['points'])


def capture(game, player, ch):
    from .models import IslandCell, IslandGame
    x, y, pts = ch['x'], ch['y'], ch['points']
    with transaction.atomic():
        game = IslandGame.objects.select_for_update().get(pk=game.pk)
        game.version += 1
        cell = IslandCell.objects.filter(game=game, x=x, y=y).first()
        scores = dict(game.scores or {})
        if cell is None:
            IslandCell.objects.create(game=game, x=x, y=y, team=player.team, by=player, ver=game.version)
            gained = True
        else:
            gained = cell.team != player.team
            if gained:
                scores[str(cell.team)] = max(0, scores.get(str(cell.team), 0) - pts)
                cell.team, cell.by = player.team, player
            cell.ver = game.version
            cell.save(update_fields=['team', 'by', 'ver'])
        if gained:
            scores[str(player.team)] = scores.get(str(player.team), 0) + pts
        game.scores = scores
        game.save(update_fields=['version', 'scores'])
        player.x, player.y = x, y
        if gained:
            player.captured += 1
            player.points += pts
        if ch.get('task'):
            player.used_tasks = (player.used_tasks or []) + [ch['task']]
        player.challenge = {}
        player.save(update_fields=['x', 'y', 'captured', 'points', 'used_tasks', 'challenge'])


def count_exit(player, kind, now=None):
    """Выход из полного экрана. На MAX_EXITS-м ученика выбивает."""
    from .models import IslandPlayer
    now = now or timezone.now()
    with transaction.atomic():
        player = IslandPlayer.objects.select_for_update().get(pk=player.pk)
        if not player.kicked:
            player.exits += 1
            player.exit_log = (player.exit_log or []) + [{'kind': kind, 'at': now.isoformat()}]
            if player.exits >= MAX_EXITS:
                player.kicked, player.challenge = True, {}
            player.save(update_fields=['exits', 'exit_log', 'kicked', 'challenge'])
    return player


def restore(player):
    """Учитель вернул выбитого ученика (например, были проблемы с компьютером)."""
    player.exits, player.kicked, player.client = 0, False, ''
    player.save(update_fields=['exits', 'kicked', 'client'])
    _bump(player.game)


# ------------------------------------------------------------------ состояние для браузера
def state(game, since=0, me=None, lang='ru', now=None):
    """Что изменилось с версии since. Клетки — только новые; игроки и счёт — всегда (их мало)."""
    now = now or timezone.now()
    game = finish_if_needed(game, now)
    players = list(game.players.select_related('student'))
    out = {
        'v': game.version, 'status': game.status, 'left': seconds_left(game, now),
        'players': [[p.id, p.x, p.y, p.team, int(p.kicked), p.student.full_name, p.captured, p.points, p.exits]
                    for p in players],
        'scores': game.scores or {}, 'teams': teams_info(game, players, lang),
    }
    if game.status != 'lobby':
        cells = game.cells.filter(ver__gt=since) if since else game.cells.all()
        out['cells'] = [[c.x, c.y, c.team] for c in cells]
    if since == 0 and game.status != 'lobby':
        out['map'] = game.map
    if me is not None:
        out['me'] = {'id': me.id, 'x': me.x, 'y': me.y, 'team': me.team, 'kicked': me.kicked, 'exits': me.exits,
                     'wait': cooldown_left(me, now), 'coins': me.coins}
    return out
