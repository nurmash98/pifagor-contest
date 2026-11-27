# -*- coding: utf-8 -*-
"""Подсказки ассистента в «Компании Пифагор» и в «Офисе выходного дня».

Уровни (каждый следующий открывается по кнопке «Ещё подсказка»):
  1 — совет по теме + разбор примера из условия            — бесплатно
  2 — где ошибка: тест, ваш вывод и что ожидалось          — бесплатно (то же видно на странице задачи)
  3 — разбор кода: типичные ошибки в коде ученика          — −10% зарплаты за эту задачу
  4 — подсказка ИИ (Claude) без готового решения            — −20% зарплаты (только если на сервере есть ключ API)
Платный уровень оплачивается один раз на задачу за рабочий день (HintUse) и только если подсказка
что-то нашла. Повторно открыть купленный уровень — бесплатно (содержимое пересчитывается по свежему коду).
"""
import ast
import hashlib
import json
import os
import re
import urllib.request

from django.core.cache import cache

COST = {3: 10, 4: 20}          # % зарплаты за задачу
COST_CAP = 50                  # больше половины зарплаты подсказки не съедают
AI_MODEL = os.environ.get('HINT_AI_MODEL', 'claude-haiku-5-5')
AI_PER_DAY = 3                 # ИИ-подсказок на одну задачу в день (защита от лишних расходов)


def ai_enabled():
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def max_level():
    return 4 if ai_enabled() else 3


def used_pct(student_id, task_id, ctx):
    from .models import HintUse
    pct = sum(COST.get(l, 0) for l in HintUse.objects.filter(student_id=student_id, task_id=task_id, ctx=ctx)
              .values_list('level', flat=True))
    return min(COST_CAP, pct)


def pct_map(ctx, student_ids=None):
    """{(student_id, task_id): % снятой зарплаты} для контекста ('room:5' / 'office:2026-11-28')."""
    from .models import HintUse
    q = HintUse.objects.filter(ctx=ctx)
    if student_ids is not None:
        q = q.filter(student_id__in=list(student_ids))
    out = {}
    for sid, tid, lvl in q.values_list('student_id', 'task_id', 'level'):
        out[(sid, tid)] = min(COST_CAP, out.get((sid, tid), 0) + COST.get(lvl, 0))
    return out


def apply_pct(pay, pct):
    return int(round(pay * (100 - min(COST_CAP, pct)) / 100))


# ------------------------------------------------------------------ уровень 3: разбор кода

def latest_code(student, task):
    """Самый свежий код ученика: черновик (автосохранение) или последняя отправка."""
    from .models import Submission
    sub = Submission.objects.filter(student=student, task=task).only('code', 'draft_code', 'draft_at', 'last_submitted_at').first()
    if sub is None:
        return ''
    if sub.draft_code and (not sub.code or (sub.draft_at and (not sub.last_submitted_at or sub.draft_at >= sub.last_submitted_at))):
        return sub.draft_code
    return sub.code or ''


def _is_num(s):
    return bool(re.fullmatch(r'-?\d+(\.\d+)?', s))


def analyze(code, task, lang='ru'):
    """Список коротких замечаний к коду (типичные ошибки школьников в Python)."""
    code = (code or '').replace('\r\n', '\n')
    out = []
    if not code.strip():
        return ['Код пока пустой — напишите хотя бы чтение входных данных.']
    ex_in = (task.input_example or '').replace('\\n', '\n').strip()
    ex_out = (task.output_example or '').replace('\\n', '\n').strip()
    in_lines = [ln for ln in ex_in.splitlines() if ln.strip()]
    in_tokens = ex_in.split()
    nums_in = bool(in_tokens) and all(_is_num(t) for t in in_tokens)
    out_tokens = ex_out.split()

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        line = (code.splitlines()[e.lineno - 1].strip() if e.lineno and e.lineno <= len(code.splitlines()) else '')
        msg = f'Синтаксическая ошибка в строке {e.lineno}: {line[:60]}'
        if 'indent' in (e.msg or '').lower():
            msg += ' — проверьте отступы (4 пробела внутри if / for / while / def).'
        elif line.rstrip().startswith(('if ', 'for ', 'while ', 'elif ', 'else', 'def ')) and not line.rstrip().endswith(':'):
            msg += ' — в конце строки не хватает двоеточия «:».'
        elif code.count('(') != code.count(')'):
            msg += ' — не хватает закрывающей скобки «)».'
        else:
            msg += f' ({e.msg}).'
        return [msg]

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def fname(c):
        return c.func.id if isinstance(c.func, ast.Name) else (c.func.attr if isinstance(c.func, ast.Attribute) else '')

    inputs = [c for c in calls if fname(c) == 'input']
    parents = {}
    for n in ast.walk(tree):
        for ch in ast.iter_child_nodes(n):
            parents[ch] = n

    # input("Введите …") — подсказка в input печатается в вывод и ломает ответ
    if any(c.args for c in inputs):
        out.append('Уберите текст внутри input("…") — он попадает в вывод, и ответ не совпадает. Пишите просто input().')

    if ex_in and not inputs and 'sys.stdin' not in code and 'open(' not in code:
        out.append('Программа не читает входные данные: добавьте input() — числа из условия нельзя вписывать в код.')

    # числа читаются как строки
    if nums_in and inputs:
        def wrapped(c):
            p = parents.get(c)
            while isinstance(p, (ast.Attribute, ast.Call)) and not (isinstance(p, ast.Call) and fname(p) in ('int', 'float', 'map', 'eval')):
                p = parents.get(p)
            return isinstance(p, ast.Call) and fname(p) in ('int', 'float', 'map', 'eval')
        raw = [c for c in inputs if not wrapped(c)]
        if raw and not re.search(r'\bint\s*\(|\bfloat\s*\(|\bmap\s*\(', code):
            out.append('input() возвращает строку: "2" + "3" = "23". Переведите в число: int(input()).')

    # несколько чисел в одной строке, а код читает по одному
    if len(in_lines) == 1 and len(in_tokens) > 1 and inputs and '.split' not in code:
        out.append(f'Во входных данных {len(in_tokens)} значения в одной строке. Читайте их сразу: '
                   'a, b = map(int, input().split()).')
    elif len(in_lines) > 1 and len(inputs) == 1 and '.split' in code and 'for' not in code and 'while' not in code \
            and all(len(ln.split()) == 1 for ln in in_lines):
        out.append('Каждое значение во входных данных — на отдельной строке. Нужен отдельный input() на каждую строку.')

    # лишний текст в выводе
    out_is_plain = bool(out_tokens) and all(_is_num(t) or t.upper() in ('YES', 'NO') for t in out_tokens)
    if out_is_plain:
        for c in calls:
            if fname(c) == 'print':
                for a in c.args:
                    s = a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
                    if isinstance(a, ast.JoinedStr):
                        s = ''.join(v.value for v in a.values if isinstance(v, ast.Constant))
                    if s and re.search(r'[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]{3,}', s) and s.strip().upper() not in ('YES', 'NO'):
                        out.append(f'Лишний текст в выводе: print("{s.strip()[:30]}…"). Выводите только ответ — как в примере.')
                        break
                else:
                    continue
                break

    # деление: / даёт дробь
    if out_tokens and all(re.fullmatch(r'-?\d+', t) for t in out_tokens) and \
            any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) for n in ast.walk(tree)) and '//' not in code \
            and 'int(' not in code.split('/', 1)[-1][:40]:
        out.append('Оператор / даёт дробь (7 / 2 = 3.5, 6 / 2 = 3.0). Для целого деления используйте //.')

    # ответ вписан в код
    if ex_in and ex_out and len(ex_out) <= 40 and re.search(r'print\(\s*[\'"]?' + re.escape(ex_out.splitlines()[0].strip()) + r'[\'"]?\s*\)', code):
        out.append('Похоже, ответ из примера вписан в print. Программа должна считать ответ для любых входных данных.')

    # бесконечный while
    for n in ast.walk(tree):
        if isinstance(n, ast.While) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name):
            var = n.test.left.id
            changed = any(isinstance(m, (ast.Assign, ast.AugAssign)) and any(
                isinstance(t, ast.Name) and t.id == var for t in (m.targets if isinstance(m, ast.Assign) else [m.target]))
                for b in n.body for m in ast.walk(b))
            if not changed:
                out.append(f'В цикле while переменная {var} не меняется — цикл будет бесконечным.')
                break

    # range(1, n) когда, скорее всего, нужно до n включительно
    for c in calls:
        if fname(c) == 'range' and len(c.args) == 2 and isinstance(c.args[0], ast.Constant) and c.args[0].value == 1 \
                and isinstance(c.args[1], ast.Name):
            out.append(f'range(1, {c.args[1].id}) не включает {c.args[1].id}. Если нужно до {c.args[1].id} включительно — range(1, {c.args[1].id} + 1).')
            break

    if lang == 'kk':
        from .templatetags.ui_i18n import UI_TRANSLATIONS as TR
        out = [TR.get(s, s) for s in out]
    return out[:4]


# ------------------------------------------------------------------ уровень 4: ИИ

AI_SYSTEM = (
    "Ты — ассистент учителя информатики в школе. Ученик 5–11 класса решает задачу на Python и застрял. "
    "Дай ОДНУ короткую подсказку (2–4 предложения), которая подтолкнёт к решению. "
    "НЕЛЬЗЯ писать готовое решение или исправленный код целиком; можно показать одну строку-пример или указать номер строки с ошибкой. "
    "Пиши просто и дружелюбно, без markdown-заголовков. Язык ответа: {lang}."
)


def ai_hint(task, student, code, fail, lang='ru'):
    """Текст подсказки от Claude или (None, причина)."""
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        return None, 'off'
    if not (code or '').strip():
        return None, 'nocode'
    h = hashlib.sha1(f'{task.id}|{lang}|{code}'.encode()).hexdigest()
    ck = f'hint:ai:{h}'
    got = cache.get(ck)
    if got:
        return got, None
    day_key = f'hint:ai:n:{student.id}:{task.id}'
    if (cache.get(day_key) or 0) >= AI_PER_DAY:
        return None, 'limit'
    title = task.get_display_title(lang) if hasattr(task, 'get_display_title') else task.title
    desc = task.get_display_description(lang) if hasattr(task, 'get_display_description') else task.description
    parts = [f'Задача: {title}\n\n{desc[:2500]}',
             f'Пример ввода:\n{(task.input_example or "")[:300]}\nПример вывода:\n{(task.output_example or "")[:300]}',
             f'Код ученика:\n```python\n{code[:4000]}\n```']
    if fail and fail.get('n'):
        parts.append(f'Не проходит тест №{fail["n"]}. Ввод: {fail.get("input", "")[:200]!r}. '
                     f'Вывод ученика: {fail.get("actual", "")[:200]!r}. Ожидалось: {fail.get("expected", "")[:200]!r}. '
                     f'Ошибка: {fail.get("error") or "неверный ответ"}.')
    body = json.dumps({
        'model': AI_MODEL, 'max_tokens': 300,
        'system': AI_SYSTEM.format(lang='казахский' if lang == 'kk' else 'русский'),
        'messages': [{'role': 'user', 'content': '\n\n'.join(parts)}],
    }).encode()
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, method='POST', headers={
        'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text').strip()
    except Exception:
        return None, 'fail'
    if not text:
        return None, 'fail'
    cache.set(ck, text, 7 * 24 * 3600)
    cache.set(day_key, (cache.get(day_key) or 0) + 1, 24 * 3600)
    return text, None


# ------------------------------------------------------------------ сборка

def last_fail(student, task, lang='ru'):
    from .models import Submission
    from .room import ERROR_TIPS
    sub = (Submission.objects.filter(student=student, task=task).exclude(autotest_total=None)
           .only('autotest_results', 'autotest_passed', 'autotest_total').first())
    if sub is None:
        return None
    for n, r in enumerate(sub.autotest_results or [], 1):
        if not r.get('ok'):
            err = str(r.get('error') or '')
            why = next((t for k, t in ERROR_TIPS if k in err), None) or (
                'Сравните свой вывод с ожидаемым — найдите, чем они отличаются.' if not err else err[:200])
            if lang == 'kk':
                from .templatetags.ui_i18n import UI_TRANSLATIONS as TR
                why = TR.get(why, why)
            return {'n': n, 'input': str(r.get('input') or '')[:200], 'expected': str(r.get('expected') or '')[:200],
                    'actual': str(r.get('actual') or '')[:200], 'error': err[:40], 'why': why,
                    'passed': sub.autotest_passed, 'total': sub.autotest_total}
    return {'n': 0, 'passed': sub.autotest_passed, 'total': sub.autotest_total}


def build(task, student, level, ctx, pay, lang='ru'):
    """Подсказка уровня level. Платный уровень записывается в HintUse, только если подсказка что-то дала."""
    from .models import HintUse
    from .room import hint as base_hint
    level = max(1, min(max_level(), int(level or 1)))
    out = {'task': task.id, 'level': level, 'max': max_level(), 'cost': COST, 'pay': pay}
    if level == 1:
        b = base_hint(task, student, lang)
        out.update(tip=b['tip'], ex_in=b['ex_in'], ex_out=b['ex_out'])
    elif level == 2:
        out['fail'] = last_fail(student, task, lang)
    elif level == 3:
        out['issues'] = analyze(latest_code(student, task), task, lang)
    else:
        text, err = ai_hint(task, student, latest_code(student, task), last_fail(student, task, lang), lang)
        out['ai'] = text
        out['ai_err'] = err
    useful = (level == 3 and out['issues']) or (level == 4 and out.get('ai'))
    if useful and level in COST:
        HintUse.objects.get_or_create(student=student, task=task, ctx=ctx, level=level)
    out['pct'] = used_pct(student.id, task.id, ctx)
    out['pay_now'] = apply_pct(pay, out['pct'])
    out['paid'] = sorted(HintUse.objects.filter(student=student, task=task, ctx=ctx).values_list('level', flat=True))
    return out
