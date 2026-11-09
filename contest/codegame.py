# -*- coding: utf-8 -*-
"""Игра «Бомба» на задачах «Что выведет код?». Вопросы — шаблоны 1 тура из infotartys
(contest/codegame_bank.json): {{a:2..9}} — случайное целое, {{op:+|-|*}} — один из вариантов,
одинаковое имя — одно значение. Ответ (вывод программы) вычисляется запуском кода в той же
песочнице, что и автотесты, поэтому он всегда верный, а у каждого ученика свои числа."""
import json
import os
import random
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .autotest import _run_one, outputs_match

TOPICS = [('@io', 'Ввод-вывод и переменные'), ('@cond', 'Условия if / else')]
TOPIC_SHORT = {'@io': 'ввод-вывод', '@cond': 'if/else'}
ENTRY_FEE = 1               # монет за вход в игру
TOPIC_LABELS = dict(TOPICS)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*:\s*([^}]+?)\s*\}\}")
RANGE_RE = re.compile(r"^(-?\d+)\s*\.\.\s*(-?\d+)$")
MAX_OUTPUT = 200
BOMB_STRIKES = 3             # ошибок до взрыва
WIRE_COLORS = ['red', 'blue', 'yellow', 'green', 'white']

_BANK = None


def bank():
    global _BANK
    if _BANK is None:
        with open(os.path.join(os.path.dirname(__file__), 'codegame_bank.json'), encoding='utf-8') as f:
            _BANK = json.load(f)
    return _BANK


def fill(code, rng):
    values = {}

    def repl(m):
        name, spec = m.group(1), m.group(2).strip()
        if name not in values:
            rm = RANGE_RE.match(spec)
            if rm:
                lo, hi = sorted((int(rm.group(1)), int(rm.group(2))))
                values[name] = str(rng.randint(lo, hi))
            else:
                values[name] = rng.choice([o.strip() for o in spec.split('|') if o.strip()] or [''])
        return values[name]
    return PLACEHOLDER_RE.sub(repl, code)


def run_output(code):
    """Вывод программы или None, если код упал, завис или вывел слишком много/ничего."""
    out, err = _run_one(code, '')
    if err:
        return None
    out = (out or '').rstrip()
    if not out or len(out) > MAX_OUTPUT:
        return None
    return out


def make_question(template, rng, attempts=8):
    for _ in range(attempts):
        code = fill(template['code'], rng)
        out = run_output(code)
        if out is not None:
            return {'title': template['title'], 'code': code, 'answer': out, 'template': template['code']}
    return None


def _distractors(q, rng, need=3):
    """Неправильные варианты для проводов: тот же шаблон с другими числами, а для чисел — соседние значения."""
    seen = {q['answer'].strip()}
    out = []
    ans = q['answer'].strip()
    if re.fullmatch(r'-?\d+', ans):
        n = int(ans)
        for d in rng.sample([1, -1, 2, -2, 3, 10, -10, n or 5], 8):
            v = str(n + d)
            if v not in seen:
                seen.add(v); out.append(v)
            if len(out) >= need:
                return out
    for _ in range(6):
        if len(out) >= need:
            break
        v = run_output(fill(q['template'], rng))
        if v and v.strip() not in seen:
            seen.add(v.strip()); out.append(v.strip())
    while len(out) < need:      # на крайний случай
        v = ans + ' ' * 0 + str(len(out) + 2)
        if v not in seen:
            seen.add(v); out.append(v)
    return out


def generate(game, rng=None):
    """Список вопросов для нового прохождения игры (у каждого ученика свои числа)."""
    rng = rng or random.Random()
    topics = set(game.topic_list()) or {t for t, _ in TOPICS}
    pool = [t for t in bank() if set(t['tags']) & topics]
    rng.shuffle(pool)
    questions = []
    for tpl in pool:
        if len(questions) >= game.questions:
            break
        q = make_question(tpl, rng)
        if q is None:
            continue
        q['kind'] = 'wires' if len(questions) % 2 else 'keypad'
        if q['kind'] == 'wires':
            options = [q['answer']] + _distractors(q, rng)
            rng.shuffle(options)
            q['options'] = options
            q['colors'] = rng.sample(WIRE_COLORS, len(options))
        q['done'] = False
        questions.append(q)
    return questions


def is_correct(q, answer):
    return outputs_match(answer or '', q['answer'])


def public_question(q, i):
    """То, что можно отдать в браузер: без правильного ответа."""
    d = {'i': i, 'title': q['title'], 'code': q['code'], 'kind': q['kind'], 'done': q.get('done', False)}
    if q['kind'] == 'wires':
        d['wires'] = [{'label': o, 'color': c} for o, c in zip(q['options'], q['colors'])]
    return d


def deadline(run):
    return run.started_at + timedelta(seconds=run.game.time_limit - run.penalty)


def seconds_left(run, now=None):
    now = now or timezone.now()
    return max(0, int((deadline(run) - now).total_seconds()))


def _finish(run, won, now):
    from .models import CoinTransaction
    run.status = 'won' if won else 'lost'
    run.finished_at = now
    if won and run.game.coins > 0:
        tx, created = CoinTransaction.objects.get_or_create(
            student=run.student, key=f'game:{run.id}',
            defaults=dict(kind='game', amount=run.game.coins, at=now, note='Бомба обезврежена'))
        if created:
            run.coins = run.game.coins
    run.save()


def forfeit(run):
    """Ученик вышел из полноэкранного режима — проигрыш."""
    from .models import CodeGameRun
    with transaction.atomic():
        run = CodeGameRun.objects.select_for_update().select_related('game', 'student').get(pk=run.pk)
        if run.status == 'playing':
            _finish(run, False, timezone.now())
    return run


def expire_if_needed(run, now=None):
    now = now or timezone.now()
    if run.status == 'playing' and now >= deadline(run):
        _finish(run, False, now)
        return True
    return False


def answer(run, index, value):
    """Обрабатывает ответ. Возвращает dict для браузера: ok, state (playing/won/lost), left, strikes, done."""
    from .models import CodeGameRun
    now = timezone.now()
    with transaction.atomic():
        run = CodeGameRun.objects.select_for_update().select_related('game', 'student').get(pk=run.pk)
        if expire_if_needed(run, now) or run.status != 'playing':
            return {'ok': False, 'state': run.status, 'left': 0, 'strikes': run.mistakes}
        qs = run.questions
        if not (0 <= index < len(qs)) or qs[index].get('done'):
            return {'ok': False, 'state': run.status, 'left': seconds_left(run, now), 'strikes': run.mistakes, 'bad': True}
        q = qs[index]
        if q['kind'] == 'wires':
            ok = str(value) in [str(k) for k, o in enumerate(q['options']) if o.strip() == q['answer'].strip()]
        else:
            ok = is_correct(q, value)
        if ok:
            q['done'] = True
            run.progress = sum(1 for x in qs if x.get('done'))
            run.questions = qs
        else:
            run.mistakes += 1
        run.save()
        if run.progress >= len(qs):
            _finish(run, True, now)
        elif run.mistakes >= BOMB_STRIKES:
            _finish(run, False, now)
        elif expire_if_needed(run, now):
            pass
        return {'ok': ok, 'state': run.status, 'left': seconds_left(run, now) if run.status == 'playing' else 0,
                'strikes': run.mistakes, 'progress': run.progress, 'coins': run.coins,
                'answer': q['answer'] if (run.status == 'lost') else None}
