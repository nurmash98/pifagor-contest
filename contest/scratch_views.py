# -*- coding: utf-8 -*-
"""Scratch-тренажёр для 1–4 классов: карта уровней, уровень с редактором блоков, проверка, прогресс для учителя."""
import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scratch_levels as sl
from .models import CoinTransaction, ScratchProgress, Student

COINS_SOLVED = 2          # уровень пройден впервые
COINS_THREE = 1           # и ещё монета за 3 звезды


def _lang(request):
    return 'kk' if request.session.get('content_lang') == 'kk' else 'ru'


def _who(request):
    """(student, preview): ученик 1–4 класса или учитель (смотрит без сохранения). Иначе (None, False)."""
    if request.user.is_staff:
        return None, True
    st = Student.objects.filter(user=request.user).first()
    if st is not None and sl.is_junior(st):
        return st, False
    return None, False


def _progress(student):
    if student is None:
        return {}
    return {p.level: p for p in ScratchProgress.objects.filter(student=student)}


def _unlocked(key, prog, preview):
    if preview:
        return True
    for _, _, _, keys in sl.TRACKS:
        if key in keys:
            i = keys.index(key)
            return i == 0 or (keys[i - 1] in prog and prog[keys[i - 1]].stars > 0)
    return False


def _next_key(key):
    for _, _, _, keys in sl.TRACKS:
        if key in keys:
            i = keys.index(key)
            return keys[i + 1] if i + 1 < len(keys) else None
    return None


@login_required
def scratch_home(request):
    student, preview = _who(request)
    if student is None and not preview:
        return redirect('all_tasks')
    lang = _lang(request)
    prog = _progress(student)
    tracks = []
    for key, ru, kk, keys in sl.TRACKS:
        items = []
        for i, k in enumerate(keys, 1):
            p = prog.get(k)
            items.append({'key': k, 'n': i, 'title': sl.BY_KEY[k]['kk' if lang == 'kk' else 'ru'],
                          'stars': p.stars if p else 0, 'open': _unlocked(k, prog, preview)})
        tracks.append({'key': key, 'title': kk if lang == 'kk' else ru, 'levels': items,
                       'stars': sum(x['stars'] for x in items), 'max': len(items) * 3})
    return render(request, 'scratch_home.html', {'tracks': tracks, 'preview': preview,
                                                 'total': sum(t['stars'] for t in tracks), 'total_max': sum(t['max'] for t in tracks)})


@login_required
def scratch_level(request, key):
    level = sl.BY_KEY.get(key)
    if level is None:
        raise Http404
    student, preview = _who(request)
    if student is None and not preview:
        return redirect('all_tasks')
    prog = _progress(student)
    if not _unlocked(key, prog, preview):
        return redirect('scratch_home')
    mine = prog.get(key)
    lang = _lang(request)
    nxt = _next_key(key)
    return render(request, 'scratch_level.html', {
        'level': level, 'title': level['kk' if lang == 'kk' else 'ru'],
        'data': json.dumps(sl.public(level, lang), ensure_ascii=False),
        'saved': json.dumps(mine.program if mine and mine.program else []),
        'stars': mine.stars if mine else 0, 'next_key': nxt, 'preview': preview,
        'track_n': next((keys.index(key) + 1 for _, _, _, keys in sl.TRACKS if key in keys), 1),
    })


@login_required
@require_POST
def scratch_check(request, key):
    level = sl.BY_KEY.get(key)
    if level is None:
        raise Http404
    student, preview = _who(request)
    if student is None and not preview:
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        raw = json.loads(request.body or b'{}').get('program')
    except (ValueError, AttributeError):
        raw = None
    program = sl.clean(raw, set(level['blocks']))
    if program is None or sl.count_blocks(program) > sl.MAX_BLOCKS:
        return JsonResponse({'error': 'bad_program'})
    res = sl.check(level, program)
    out = dict(res, coins=0, best_stars=res['stars'], next=_next_key(key))
    if student is None:                       # учитель смотрит — ничего не сохраняем
        return JsonResponse(out)
    if student is not None and not _unlocked(key, _progress(student), False):
        return JsonResponse({'error': 'locked'})
    now = timezone.now()
    p, _ = ScratchProgress.objects.get_or_create(student=student, level=key)
    p.attempts += 1
    if res['ok'] and res['stars'] >= p.stars:
        if res['stars'] > p.stars or not p.program or res['blocks'] < p.blocks:
            p.program, p.blocks = program, res['blocks']
        p.stars = res['stars']
        p.solved_at = p.solved_at or now
    p.save()
    out['best_stars'] = p.stars
    title = level['ru']
    if res['ok']:
        _, made = CoinTransaction.objects.get_or_create(student=student, key=f'scratch:{key}',
                                                        defaults=dict(kind='scratch', amount=COINS_SOLVED, at=now, note=f'Scratch: {title}'))
        out['coins'] += COINS_SOLVED if made else 0
        if res['stars'] == 3:
            _, made = CoinTransaction.objects.get_or_create(student=student, key=f'scratch3:{key}',
                                                            defaults=dict(kind='scratch', amount=COINS_THREE, at=now, note=f'Scratch ★★★: {title}'))
            out['coins'] += COINS_THREE if made else 0
    return JsonResponse(out)


@login_required
def scratch_teacher(request):
    """Учитель: прогресс классов 1–4 по уровням тренажёра."""
    if not request.user.is_staff:
        return redirect('scratch_home')
    from .quarters import grade_of
    from .views import _managed_classes
    scope = _managed_classes(request.user)
    classes = sorted({c for c in (Student.objects.values_list('school_class', flat=True) if scope is None else scope)
                      if c and grade_of(c).isdigit() and 1 <= int(grade_of(c)) <= 4},
                     key=lambda c: (int(grade_of(c)), c))
    cls = request.GET.get('c') or (classes[0] if classes else None)
    if cls not in classes:
        cls = classes[0] if classes else None
    keys = [k for _, _, _, ks in sl.TRACKS for k in ks]
    rows = []
    if cls:
        students = list(Student.objects.filter(school_class=cls).order_by('full_name'))
        prog = {}
        for p in ScratchProgress.objects.filter(student__in=students):
            prog[(p.student_id, p.level)] = p
        for s in students:
            cells = [prog.get((s.id, k)) for k in keys]
            rows.append({'name': s.full_name, 'cells': [c.stars if c else None for c in cells],
                         'solved': sum(1 for c in cells if c and c.stars), 'stars': sum(c.stars for c in cells if c),
                         'last': max((c.updated_at for c in cells if c), default=None)})
    return render(request, 'scratch_teacher.html', {
        'classes': classes, 'cls': cls, 'rows': rows, 'keys': keys,
        'tracks': [(ru, len(ks)) for _, ru, _, ks in sl.TRACKS], 'max_stars': len(keys) * 3, 'n_levels': len(keys),
    })
