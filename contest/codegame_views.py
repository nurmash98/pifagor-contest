# -*- coding: utf-8 -*-
"""Игры «Что выведет код?»: учитель создаёт игру (режим, темы, ученики), ученик играет."""
import random

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db.models import Min
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import codegame as cg
from .models import CodeGame, CodeGameRun, Student
from .names import sort_students

DEFAULT_TIME = {'escape': 600, 'bomb': 300}


def _can_edit(user, game):
    return user.is_superuser or game.owner_id == user.id


def _my_students(user):
    from .views import _managed_classes
    classes = _managed_classes(user)
    qs = Student.objects.select_related('user')
    if classes is not None:
        qs = qs.filter(school_class__in=classes)
    return sort_students(qs, by_class=True)


def _int(raw, default, lo, hi):
    try:
        return min(hi, max(lo, int(raw)))
    except (TypeError, ValueError):
        return default


def _save_form(request, game):
    game.title = (request.POST.get('title') or '').strip()[:120] or 'Игра'
    game.mode = request.POST.get('mode') if request.POST.get('mode') in ('escape', 'bomb') else 'escape'
    topics = [t for t in request.POST.getlist('topics') if t in cg.TOPIC_LABELS]
    game.topics = ','.join(topics or list(cg.TOPIC_LABELS))
    game.questions = _int(request.POST.get('questions'), 5, 1, 15)
    game.time_limit = _int(request.POST.get('minutes'), DEFAULT_TIME[game.mode] // 60, 1, 60) * 60
    game.coins = _int(request.POST.get('coins'), 20, 0, 200)
    game.is_active = bool(request.POST.get('is_active'))
    game.save()
    allowed = {s.id for s in _my_students(request.user)}
    chosen = {int(x) for x in request.POST.getlist('students') if x.isdigit()} & allowed
    keep_foreign = [s.id for s in game.students.all() if s.id not in allowed]   # чужие классы не трогаем
    game.students.set(list(chosen) + keep_foreign)


@staff_member_required
def games_manage(request):
    if request.method == 'POST':
        game = CodeGame(owner=request.user)
        _save_form(request, game)
        messages.success(request, f'Игра «{game.title}» создана.')
        return redirect('codegame_edit', game_id=game.id)
    games = CodeGame.objects.all() if request.user.is_superuser else CodeGame.objects.filter(owner=request.user)
    return render(request, 'codegames_manage.html', {
        'games': games.prefetch_related('students'), 'topics': cg.TOPICS, 'students': _my_students(request.user),
        'game': None,
    })


@staff_member_required
def game_edit(request, game_id):
    game = get_object_or_404(CodeGame, id=game_id)
    if not _can_edit(request.user, game):
        messages.error(request, 'Это не ваша игра.')
        return redirect('codegames_manage')
    if request.method == 'POST':
        _save_form(request, game)
        messages.success(request, 'Сохранено.')
        return redirect('codegame_edit', game_id=game.id)
    runs = list(game.runs.select_related('student').filter(status__in=['won', 'lost'])[:300])
    best = {}
    for r in runs:
        if r.status == 'won':
            d = r.duration()
            if r.student_id not in best or d < best[r.student_id]['time']:
                best[r.student_id] = {'student': r.student, 'time': d}
    stats = {}
    for r in runs:
        s = stats.setdefault(r.student_id, {'student': r.student, 'won': 0, 'lost': 0})
        s[r.status] += 1
    rows = []
    for sid, s in stats.items():
        s['best'] = best.get(sid, {}).get('time')
        rows.append(s)
    rows.sort(key=lambda s: (s['best'] is None, s['best'] or 0))
    return render(request, 'codegames_manage.html', {
        'game': game, 'topics': cg.TOPICS, 'students': _my_students(request.user),
        'chosen': set(game.students.values_list('id', flat=True)), 'rows': rows,
    })


@staff_member_required
@require_POST
def game_delete(request, game_id):
    game = get_object_or_404(CodeGame, id=game_id)
    if _can_edit(request.user, game):
        game.delete()
        messages.success(request, 'Игра удалена.')
    return redirect('codegames_manage')


def _student(request):
    st = Student.objects.filter(user=request.user).first()
    if st is None:
        raise Http404
    return st


@login_required
def games(request):
    if request.user.is_staff:
        return redirect('codegames_manage')
    st = _student(request)
    my = CodeGame.objects.filter(is_active=True, students=st)
    today = timezone.localdate().isoformat()
    from .models import CoinTransaction
    paid = set(CoinTransaction.objects.filter(student=st, key__startswith='game:', key__endswith=today)
               .values_list('key', flat=True))
    items = []
    for g in my:
        won = [r.duration() for r in g.runs.filter(student=st, status='won')]
        items.append({'game': g, 'best': min(won) if won else None, 'wins': len(won),
                      'paid_today': f'game:{g.id}:{today}' in paid,
                      'topics': ', '.join(cg.TOPIC_LABELS.get(t, t) for t in g.topic_list())})
    return render(request, 'codegames.html', {'items': items})


@login_required
@require_POST
def game_start(request, game_id):
    st = _student(request)
    game = get_object_or_404(CodeGame, id=game_id, is_active=True, students=st)
    now = timezone.now()
    current = CodeGameRun.objects.filter(game=game, student=st, status='playing').first()
    if current and not cg.expire_if_needed(current, now):
        return redirect('codegame_play', run_id=current.id)
    questions = cg.generate(game, random.Random())
    if not questions:
        messages.error(request, 'Не получилось собрать вопросы. Сообщите учителю.')
        return redirect('codegames')
    run = CodeGameRun.objects.create(game=game, student=st, questions=questions, started_at=now)
    return redirect('codegame_play', run_id=run.id)


@login_required
def game_play(request, run_id):
    st = _student(request)
    run = get_object_or_404(CodeGameRun.objects.select_related('game'), id=run_id, student=st)
    cg.expire_if_needed(run)
    qs = [cg.public_question(q, i) for i, q in enumerate(run.questions)]
    return render(request, 'codegame_play.html', {
        'run': run, 'game': run.game, 'questions': qs, 'left': cg.seconds_left(run) if run.status == 'playing' else 0,
        'strikes_max': cg.BOMB_STRIKES, 'penalty': cg.ESCAPE_PENALTY, 'duration': run.duration(),
    })


@login_required
@require_POST
def game_answer(request, run_id):
    st = _student(request)
    run = get_object_or_404(CodeGameRun, id=run_id, student=st)
    try:
        index = int(request.POST.get('index', '-1'))
    except ValueError:
        index = -1
    return JsonResponse(cg.answer(run, index, request.POST.get('answer', '')))
