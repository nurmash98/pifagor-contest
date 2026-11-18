# -*- coding: utf-8 -*-
"""Признаки того, что решение могло быть написано ИИ (только подсказки учителю, не приговор).

Две проверки (сравнения кода между учениками нет — по просьбе учителя):
  2. «Не похоже на самого ученика» — в решении сразу несколько привычек, которых не было
     ни в одном из его прошлых решений (функции, f-строки, комментарии, длинные имена…).
     Кто уже пользовался, например, списковыми включениями, — для того это не новость.
  3. «Приёмы не по уровню» — то, что в этом классе обычно ещё не проходят: lambda,
     try/except (списковые включения многие ученики знают — их не считаем), if __name__ == '__main__', аннотации типов, docstring, английские комментарии.
Сам код никуда не отправляется — всё считается на сервере."""
import ast
import re

from .quarters import grade_of

MIN_HISTORY = 5            # прошлых решений, чтобы сравнивать «на себя не похоже»
NEW_HABITS_MIN = 2         # сколько новых привычек сразу — уже подозрительно
DAYS = 60                  # на странице «Подозрительные» смотрим решения за последние 60 дней

# приём → (название для учителя, с какого класса это уже нормально; 99 — для школы всегда необычно)
ADVANCED = {
    'lambda': ('lambda-функции', 10),
    'try': ('try / except', 9),
    'main_guard': ("if __name__ == '__main__'", 99),
    'annotations': ('аннотации типов (x: int, -> int)', 99),
    'docstring': ('docstring — описание функции в тройных кавычках', 99),
    'class': ('классы (class)', 10),
    'decorator': ('декораторы (@…)', 99),
    'walrus': ('оператор :=', 99),
    'english_comments': ('комментарии на английском', 99),
    'stdin_read': ('sys.stdin.read / readline', 9),
}

HABITS = {
    'def': 'свои функции (def)',
    'fstring': 'f-строки',
    'comments': 'комментарии',
    'long_names': 'длинные «взрослые» имена переменных',
    'format': '.format() / % для вывода',
    'comprehension': 'списковые включения',
    'main_guard': "if __name__ == '__main__'",
    'docstring': 'docstring',
}

_EN_COMMENT = re.compile(r"^[A-Za-z][A-Za-z0-9 ,.'()-]{8,}$")


def _tree(code):
    try:
        return ast.parse(code or '')
    except (SyntaxError, ValueError):
        return None


def features(code):
    """Набор признаков кода (имена из ADVANCED и HABITS)."""
    out = set()
    code = code or ''
    tree = _tree(code)
    comments = [ln.split('#', 1)[1].strip() for ln in code.splitlines() if '#' in ln and not ln.strip().startswith(('"', "'"))]
    comments = [c for c in comments if c]
    if len(comments) >= 2:
        out.add('comments')
    if sum(1 for c in comments if _EN_COMMENT.match(c)) >= 2:
        out.add('english_comments')
    if re.search(r'\.format\(|%\s*\(|"\s*%\s*\w', code):
        out.add('format')
    if 'sys.stdin' in code:
        out.add('stdin_read')
    if tree is None:
        return out
    names = set()
    for node in ast.walk(tree):
        t = type(node).__name__
        if t == 'Lambda':
            out.add('lambda')
        elif t in ('ListComp', 'DictComp', 'SetComp', 'GeneratorExp'):
            out.add('comprehension')
        elif t in ('Try', 'TryStar'):
            out.add('try')
        elif t == 'ClassDef':
            out.add('class')
        elif t == 'NamedExpr':
            out.add('walrus')
        elif t == 'JoinedStr':
            out.add('fstring')
        elif t == 'AnnAssign':
            out.add('annotations')
        elif t in ('FunctionDef', 'AsyncFunctionDef'):
            out.add('def')
            if node.returns is not None or any(a.annotation is not None for a in node.args.args):
                out.add('annotations')
            if node.decorator_list:
                out.add('decorator')
            if ast.get_docstring(node):
                out.add('docstring')
        elif t == 'If':
            src = ast.dump(node.test)
            if "'__name__'" in src and "'__main__'" in src:
                out.add('main_guard')
        elif t == 'Name' and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    if ast.get_docstring(tree):
        out.add('docstring')
    long = [n for n in names if len(n) >= 8 and ('_' in n or n.lower() != n)]
    if len(long) >= 2:
        out.add('long_names')
    return out


def advanced_for(code, school_class):
    """Приёмы не по уровню этого класса: [(ключ, название)]."""
    g = grade_of(school_class)
    grade = int(g) if g.isdigit() else 11
    return [(k, ADVANCED[k][0]) for k in sorted(features(code)) if k in ADVANCED and grade < ADVANCED[k][1]]


def new_habits(code, history):
    """Привычки, которых не было ни в одном прошлом решении ученика. history — список кодов."""
    if len(history) < MIN_HISTORY:
        return []
    seen = set()
    for h in history:
        seen |= features(h)
    now = features(code)
    return [(k, HABITS[k]) for k in sorted(now - seen) if k in HABITS]


def analyse(students):
    """Признаки по ученикам: {student_id: [{'sub': Submission, 'reasons': [текст, …]}]}.
    Смотрим решения, которые засчитаны (7 баллов и выше)."""
    from .models import Submission
    students = list(students)
    by_id = {s.id: s for s in students}
    from datetime import timedelta
    from django.utils import timezone
    since = timezone.now() - timedelta(days=DAYS)
    subs = list(Submission.objects.filter(student__in=students, score__gte=7, updated_at__gte=since).exclude(code='').exclude(code__isnull=True)
                .select_related('task').order_by('student_id', 'created_at'))
    out, history = {}, {}
    for s in subs:
        st = by_id[s.student_id]
        reasons = []
        adv = advanced_for(s.code, st.school_class)
        if len(adv) >= 2 or any(k in ('main_guard', 'annotations', 'docstring', 'english_comments') for k, _ in adv):
            reasons.append('Приёмы не по уровню ' + (grade_of(st.school_class) or '') + ' класса: ' + ', '.join(t for _, t in adv))
        new = new_habits(s.code, history.get(s.student_id, []))
        if len(new) >= NEW_HABITS_MIN:
            reasons.append('Не похоже на прошлые решения ученика — впервые: ' + ', '.join(t for _, t in new))
        if reasons:
            out.setdefault(s.student_id, []).append({'sub': s, 'reasons': reasons})
        history.setdefault(s.student_id, []).append(s.code)
    return out


def for_submission(sub):
    """Признаки для одного решения (страница проверки)."""
    from .models import Submission
    st = sub.student
    reasons = []
    adv = advanced_for(sub.code, st.school_class)
    if adv:
        reasons.append('Приёмы не по уровню ' + (grade_of(st.school_class) or '') + ' класса: ' + ', '.join(t for _, t in adv))
    history = list(Submission.objects.filter(student=st, created_at__lt=sub.created_at).exclude(code='').exclude(code__isnull=True)
                   .values_list('code', flat=True))
    new = new_habits(sub.code, history)
    if new:
        reasons.append('Впервые у этого ученика: ' + ', '.join(t for _, t in new))
    return reasons


def analyse_cached(students, seconds=600):
    """analyse() с кешем на 10 минут — страница «Подозрительные» открывается быстро и не грузит сервер."""
    import hashlib
    from django.core.cache import cache
    students = list(students)
    key = 'ai_signals:' + hashlib.md5(','.join(str(s.id) for s in sorted(students, key=lambda s: s.id)).encode()).hexdigest()
    data = cache.get(key)
    if data is None:
        res = analyse(students)
        data = {sid: [{'sub_id': it['sub'].id, 'reasons': it['reasons']} for it in items] for sid, items in res.items()}
        cache.set(key, data, seconds)
    from .models import Submission
    subs = Submission.objects.select_related('task').in_bulk([it['sub_id'] for items in data.values() for it in items])
    return {sid: [{'sub': subs[it['sub_id']], 'reasons': it['reasons']} for it in items if it['sub_id'] in subs]
            for sid, items in data.items()}
