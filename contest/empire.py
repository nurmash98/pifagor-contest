# -*- coding: utf-8 -*-
"""Сложный вариант игры — «Империя» (как Civilization). Карта из шестиугольников, команды — империи
из истории Казахстана. В каждой команде один хан 👑 (остальные — сарбазы): он строит столицу, назначает
нового хана (не больше MAX_KHAN_CHANGES раз за игру) и решает с другими ханами — союз или война.

Начало: хан выбирает одну из своих 7 клеток под столицу — пока столицы нет, команда не ходит;
когда столица построена, вся команда встаёт в неё. Позже хан может перенести столицу в свой город.

Правила захвата:
  • ничья клетка — задача, как в «Острове» (трава — «что выведет код», лес — программа уровня A);
  • вражеская клетка — на уровень сложнее и только с края своей земли; во время перемирия — нельзя;
  • по своей и союзной земле ходят свободно, союзную землю захватить нельзя;
  • город (особая клетка) — сложная задача уровня C: клетка и 6 клеток вокруг становятся вашими
    (кроме союзных клеток и чужих столиц);
  • вражеская столица — тоже задача уровня C, но забирает ВСЮ землю той империи. Она начинает с нуля:
    ей сразу даются 7 ничьих клеток (если ничьих не хватает — недостающие забираются у захватчика),
    и её хан снова выбирает столицу.
Очки: клетка 1, город 5, своя столица ещё +5."""
import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

GRASS, FOREST, CITY, WATER = '.', 'f', 'C', '~'
CITY_PTS = 5
CAPITAL_PTS = 5
START_CELLS = 7
MAX_KHAN_CHANGES = 3
TRUCE = 60                      # секунд после разрыва союза нельзя нападать друг на друга
CITY_LEVEL = 4                  # задача уровня C

# (ключ, қазақша, по-русски) — государства на территории Казахстана
EMPIRES = [
    ('saka', 'Сақ тайпалары', 'Саки'),
    ('usun', 'Үйсін мемлекеті', 'Усуни'),
    ('kangly', 'Қаңлы мемлекеті', 'Канглы'),
    ('hun', 'Ғұн империясы', 'Гунны (Хунну)'),
    ('turk', 'Түрік қағанаты', 'Тюркский каганат'),
    ('turgesh', 'Түргеш қағанаты', 'Тюргешский каганат'),
    ('karluk', 'Қарлұқ қағанаты', 'Карлукский каганат'),
    ('oguz', 'Оғыз мемлекеті', 'Огузское государство'),
    ('kimak', 'Қимақ қағанаты', 'Кимакский каганат'),
    ('karakhan', 'Қарахан мемлекеті', 'Караханиды'),
    ('kypchak', 'Қыпшақ хандығы', 'Кыпчакское ханство'),
    ('naiman', 'Найман хандығы', 'Найманы'),
    ('golden', 'Алтын Орда', 'Золотая Орда'),
    ('ak', 'Ақ Орда', 'Ак Орда'),
    ('kok', 'Көк Орда', 'Кок Орда'),
    ('mogol', 'Моғолстан', 'Моголистан'),
    ('nogai', 'Ноғай Ордасы', 'Ногайская Орда'),
    ('abulkhair', 'Әбілқайыр хандығы', 'Ханство Абулхаира'),
    ('kazakh', 'Қазақ хандығы', 'Казахское ханство'),
]
EMPIRE_BY_KEY = {k: (kk, ru) for k, kk, ru in EMPIRES}


def is_empire(game):
    return getattr(game, 'variant', 'island') == 'empire'


# ------------------------------------------------------------------ шестиугольники (ряды со сдвигом: нечётные — вправо)
def hex_neighbors(x, y):
    """Соседи по порядку сторон: В, ЮВ, ЮЗ, З, СЗ, СВ (тот же порядок рисует браузер)."""
    if y % 2 == 0:
        return [(x + 1, y), (x, y + 1), (x - 1, y + 1), (x - 1, y), (x - 1, y - 1), (x, y - 1)]
    return [(x + 1, y), (x + 1, y + 1), (x, y + 1), (x - 1, y), (x, y - 1), (x + 1, y - 1)]


def _cube(x, y):
    q = x - (y - (y & 1)) // 2
    return q, y, -q - y


def hex_dist(a, b):
    (q1, r1, s1), (q2, r2, s2) = _cube(*a), _cube(*b)
    return max(abs(q1 - q2), abs(r1 - r2), abs(s1 - s2))


# ------------------------------------------------------------------ данные империи
def name_of(key, lang='ru'):
    kk, ru = EMPIRE_BY_KEY.get(key, (key, key))
    return kk if lang == 'kk' else ru


def _t(game, team):
    return (game.empire or {}).get('teams', {}).get(str(team), {})


def khan_id(game, team):
    return _t(game, team).get('khan')


def capital(game, team):
    c = _t(game, team).get('capital')
    return tuple(c) if c else None


def capitals(game):
    return {tuple(v['capital']): int(t) for t, v in (game.empire or {}).get('teams', {}).items() if v.get('capital')}


def _key(a, b):
    a, b = sorted((int(a), int(b)))
    return f'{a}-{b}'


def relation(game, a, b):
    if a == b:
        return 'self'
    return (game.empire or {}).get('rel', {}).get(_key(a, b), {}).get('s', 'enemy')


def truce_left(game, a, b, now=None):
    r = (game.empire or {}).get('rel', {}).get(_key(a, b), {})
    if not r.get('truce'):
        return 0
    from datetime import datetime
    end = datetime.fromisoformat(r['truce'])
    return max(0, int((end - (now or timezone.now())).total_seconds()))


def setup(game, players, rng):
    """Старт: имена империй (что выбрал учитель, остальные — случайно, без повторов) и случайный хан в каждой команде."""
    teams = sorted({p.team for p in players})
    preset = {int(k): v for k, v in (game.empire or {}).get('names', {}).items() if v in EMPIRE_BY_KEY}
    pool = [k for k, _, _ in EMPIRES if k not in preset.values()]
    rng.shuffle(pool)
    meta = {'teams': {}, 'rel': {}, 'offers': [], 'names': {}}
    for t in teams:
        key = preset.get(t) or (pool.pop() if pool else EMPIRES[t % len(EMPIRES)][0])
        members = [p for p in players if p.team == t]
        khan = rng.choice(members) if members else None
        meta['teams'][str(t)] = {'name': key, 'khan': khan.id if khan else None, 'changes': 0, 'capital': None, 'built': False}
        meta['names'][str(t)] = key
    return meta


def recount(game, cells=None):
    """Очки команд заново по клеткам: клетка 1, город 5, своя столица ещё +5."""
    cells = cells if cells is not None else list(game.cells.all())
    from .island import terrain
    scores = {t: 0 for t in (game.empire or {}).get('teams', {})}
    owner = {}
    for c in cells:
        owner[(c.x, c.y)] = c.team
        scores[str(c.team)] = scores.get(str(c.team), 0) + (CITY_PTS if terrain(game, c.x, c.y) == CITY else 1)
    for pos, t in capitals(game).items():
        if owner.get(pos) == t:
            scores[str(t)] = scores.get(str(t), 0) + CAPITAL_PTS
    return scores


# ------------------------------------------------------------------ ход
def move_rule(game, player, x, y, owner, now=None):
    """Что будет при шаге на (x, y): {'walk': True} | {'error': ...} | {'level', 'points', 'enemy', 'city'}."""
    from .island import POINTS, terrain, _owner
    if not capital(game, player.team):
        return {'error': 'wait_capital'}          # пока хан не выбрал столицу, команда стоит
    if owner == player.team or (owner is not None and relation(game, owner, player.team) == 'ally'):
        return {'walk': True}
    if owner is not None:
        if truce_left(game, owner, player.team, now):
            return {'error': 'truce'}
        if _owner(game, player.x, player.y) != player.team:
            return {'error': 'border'}            # нападать можно только со своей земли
    kind = terrain(game, x, y)
    city = kind == CITY or (x, y) in capitals(game)
    if city:
        return {'level': CITY_LEVEL, 'points': CITY_PTS, 'enemy': owner is not None, 'city': True}
    level = (2 if kind == FOREST else 1) + (1 if owner is not None else 0)
    return {'level': level, 'points': 1, 'enemy': owner is not None, 'city': False}


def capture(game, player, ch):
    """Захват. Город или столица — ещё и 6 клеток вокруг. Чужие фишки на отнятых клетках уходят домой."""
    from .models import IslandCell, IslandGame, IslandPlayer
    from .island import terrain
    x, y = ch['x'], ch['y']
    with transaction.atomic():
        game = IslandGame.objects.select_for_update().get(pk=game.pk)
        game.version += 1
        cells = {(c.x, c.y): c for c in IslandCell.objects.filter(game=game)}
        caps = capitals(game)
        before = recount(game, list(cells.values())).get(str(player.team), 0)
        victim = caps.get((x, y))
        if victim is not None and victim != player.team:          # взяли столицу — вся земля той империи наша
            targets = [p for p, c in cells.items() if c.team == victim]
            if (x, y) not in targets:
                targets.append((x, y))
        else:
            targets = [(x, y)]
        if victim is None and ch.get('city'):
            for n in hex_neighbors(x, y):
                if terrain(game, *n) == WATER or n in caps:
                    continue
                c = cells.get(n)
                if c is not None and c.team != player.team and relation(game, c.team, player.team) == 'ally':
                    continue
                targets.append(n)
        gained = []
        for pos in targets:
            c = cells.get(pos)
            if c is None:
                c = IslandCell.objects.create(game=game, x=pos[0], y=pos[1], team=player.team, by=player, ver=game.version)
                cells[pos] = c
                gained.append(pos)
            elif c.team != player.team:
                c.team, c.by, c.ver = player.team, player, game.version
                c.save(update_fields=['team', 'by', 'ver'])
                gained.append(pos)
        if victim is not None and victim != player.team:
            _restart(game, victim, player.team, cells)
        scores = recount(game, list(cells.values()))
        game.scores = scores
        game.save(update_fields=['version', 'scores', 'empire'])
        # фишки чужих империй на отнятых клетках — в свою столицу (или на любую свою клетку)
        lost = set(gained)
        for other in IslandPlayer.objects.filter(game=game).exclude(team=player.team):
            if victim is not None and other.team == victim:
                continue                                         # их уже расставили на новые клетки
            if (other.x, other.y) in lost:
                home = capital(game, other.team)
                if not home or cells.get(home) is None or cells[home].team != other.team:
                    own = [p for p, c in cells.items() if c.team == other.team]
                    home = random.choice(own) if own else None
                if home:
                    other.x, other.y = home
                    other.challenge = {}
                    other.save(update_fields=['x', 'y', 'challenge'])
        player.x, player.y = x, y
        if gained:
            player.captured += len(gained)
            player.points += max(1, scores.get(str(player.team), 0) - before)
        if ch.get('task'):
            player.used_tasks = (player.used_tasks or []) + [ch['task']]
        player.challenge = {}
        player.save(update_fields=['x', 'y', 'captured', 'points', 'used_tasks', 'challenge'])
    return len(gained)


def _restart(game, victim, captor, cells):
    """Империя потеряла столицу: начинает с нуля на 7 ничьих клетках (не хватает — берём у захватчика).
    Хан снова выбирает столицу; игроки команды встают на новые клетки."""
    from .models import IslandCell, IslandPlayer
    from .island import _start_zones
    rng = random.Random()
    t = game.empire['teams'][str(victim)]
    t['capital'] = None
    t['restarts'] = t.get('restarts', 0) + 1
    zone = _start_zones(game, [START_CELLS], rng, taken=tuple(cells))[0]
    if len(zone) < START_CELLS:                                 # ничьих мало — недостающие у захватчика
        cap = capital(game, captor)
        pool = [p for p, c in cells.items() if c.team == captor and p != cap and p not in zone]
        anchor = zone[0] if zone else (max(pool, key=lambda p: hex_dist(p, cap)) if pool and cap else (pool[0] if pool else None))
        pool.sort(key=lambda p: hex_dist(p, anchor) if anchor else 0)
        zone += pool[:START_CELLS - len(zone)]
    for pos in zone:
        c = cells.get(pos)
        if c is None:
            cells[pos] = IslandCell.objects.create(game=game, x=pos[0], y=pos[1], team=victim, ver=game.version)
        else:
            c.team, c.by, c.ver = victim, None, game.version
            c.save(update_fields=['team', 'by', 'ver'])
    members = list(IslandPlayer.objects.filter(game=game, team=victim))
    for i, m in enumerate(members):
        if zone:
            m.x, m.y = zone[i % len(zone)]
        m.challenge = {}
        m.save(update_fields=['x', 'y', 'challenge'])


def _send_home(game, team, host):
    """Союз разорван: игроки команды team, стоящие на земле бывшего союзника host, переносятся
    на случайную клетку своей империи (иначе они застревают — по вражеской земле не ходят)."""
    from .models import IslandCell, IslandPlayer
    host_cells = set(IslandCell.objects.filter(game=game, team=host).values_list('x', 'y'))
    stuck = [m for m in IslandPlayer.objects.filter(game=game, team=team) if (m.x, m.y) in host_cells]
    if not stuck:
        return
    home = list(IslandCell.objects.filter(game=game, team=team).values_list('x', 'y'))
    if not home:
        return
    rng = random.Random()
    for m in stuck:
        m.x, m.y = rng.choice(home)
        m.challenge = {}                           # начатая задача на чужой клетке отменяется
        m.save(update_fields=['x', 'y', 'challenge'])


# ------------------------------------------------------------------ действия хана
def _locked(game):
    from .models import IslandGame
    return IslandGame.objects.select_for_update().get(pk=game.pk)


def build_capital(game, player, x=None, y=None):
    """Хан ставит столицу на своей клетке (x, y), по умолчанию — где стоит.
    Столицы ещё нет (начало игры или империю захватили) — любая своя клетка, и вся команда встаёт туда.
    Столица есть — её можно перенести только в свой город."""
    from .models import IslandPlayer
    from .island import _owner, terrain
    with transaction.atomic():
        game = _locked(game)
        if game.status != 'running':
            return {'error': 'over'}
        t = game.empire['teams'].get(str(player.team))
        if not t or t.get('khan') != player.id:
            return {'error': 'notkhan'}
        x = player.x if x is None else x
        y = player.y if y is None else y
        if _owner(game, x, y) != player.team:
            return {'error': 'not_own'}
        first = not t.get('capital')
        if not first:
            if [x, y] == t['capital']:
                return {'error': 'has_capital'}
            if terrain(game, x, y) != CITY:
                return {'error': 'need_city'}
        t['capital'], t['built'] = [x, y], True
        game.scores = recount(game)
        game.version += 1
        game.save(update_fields=['empire', 'scores', 'version'])
        if first:                                             # вся команда начинает из столицы
            IslandPlayer.objects.filter(game=game, team=player.team).update(x=x, y=y, challenge={})
    return {'ok': True, 'first': first}


def appoint(game, player, target_id):
    """Хан передаёт корону другому игроку своей команды. Не больше MAX_KHAN_CHANGES раз за игру."""
    from .models import IslandPlayer
    with transaction.atomic():
        game = _locked(game)
        if game.status != 'running':
            return {'error': 'over'}
        t = game.empire['teams'].get(str(player.team))
        if not t or t.get('khan') != player.id:
            return {'error': 'notkhan'}
        if t.get('changes', 0) >= MAX_KHAN_CHANGES:
            return {'error': 'no_changes'}
        target = IslandPlayer.objects.filter(game=game, pk=target_id, team=player.team, kicked=False).exclude(pk=player.pk).first()
        if target is None:
            return {'error': 'bad_target'}
        t['khan'], t['changes'] = target.id, t.get('changes', 0) + 1
        game.version += 1
        game.save(update_fields=['empire', 'version'])
    return {'ok': True}


def on_kicked(game, player):
    """Хана выбило за выходы — корона сама переходит случайному игроку команды (смена не считается)."""
    from .models import IslandPlayer
    with transaction.atomic():
        game = _locked(game)
        t = (game.empire or {}).get('teams', {}).get(str(player.team))
        if not t or t.get('khan') != player.id:
            return
        mates = list(IslandPlayer.objects.filter(game=game, team=player.team, kicked=False).exclude(pk=player.pk))
        t['khan'] = random.choice(mates).id if mates else None
        game.version += 1
        game.save(update_fields=['empire', 'version'])


def diplomacy(game, player, action, other, now=None):
    """Союзы ханов: propose → у другого хана «Принять / Отклонить»; break — союз разорван, TRUCE секунд перемирия."""
    now = now or timezone.now()
    with transaction.atomic():
        game = _locked(game)
        if game.status != 'running':
            return {'error': 'over'}
        me = player.team
        t = game.empire['teams'].get(str(me))
        if not t or t.get('khan') != player.id:
            return {'error': 'notkhan'}
        if str(other) not in game.empire['teams'] or other == me:
            return {'error': 'bad_target'}
        offers = [o for o in game.empire.get('offers', []) if o]
        rel = game.empire.setdefault('rel', {})
        k = _key(me, other)
        if action == 'propose':
            if relation(game, me, other) == 'ally':
                return {'error': 'already'}
            if [other, me] in offers:                        # он уже предлагал — сразу союз
                action = 'accept'
            elif [me, other] not in offers:
                offers.append([me, other])
        if action == 'accept':
            if [other, me] not in offers:
                return {'error': 'no_offer'}
            rel[k] = {'s': 'ally', 'truce': None}
            offers = [o for o in offers if sorted(o) != sorted([me, other])]
        elif action == 'decline':
            offers = [o for o in offers if o != [other, me]]
        elif action == 'cancel':
            offers = [o for o in offers if o != [me, other]]
        elif action == 'break':
            if relation(game, me, other) != 'ally':
                return {'error': 'not_ally'}
            rel[k] = {'s': 'enemy', 'truce': (now + timedelta(seconds=TRUCE)).isoformat()}
            _send_home(game, me, other)
            _send_home(game, other, me)
        game.empire['offers'] = offers
        game.version += 1
        game.save(update_fields=['empire', 'version'])
    return {'ok': True}


# ------------------------------------------------------------------ состояние для браузера
def public(game, players, lang='ru', now=None):
    now = now or timezone.now()
    meta = game.empire or {}
    teams = {}
    for t, v in meta.get('teams', {}).items():
        changes = v.get('changes', 0)
        teams[t] = {'name': name_of(v.get('name'), lang), 'khan': v.get('khan'), 'capital': v.get('capital'),
                    'built': v.get('built', False), 'changes_left': max(0, MAX_KHAN_CHANGES - changes)}
    rel = []
    ids = sorted(int(t) for t in teams)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            rel.append([a, b, relation(game, a, b), truce_left(game, a, b, now)])
    return {'teams': teams, 'rel': rel, 'offers': meta.get('offers', []), 'max_changes': MAX_KHAN_CHANGES}
