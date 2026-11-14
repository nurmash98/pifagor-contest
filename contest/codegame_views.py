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



def _can_edit(user, game):
    return user.is_superuser or game.owner_id == user.id


def _my_students(user):
    from .views import _managed_classes
    classes = _managed_classes(user)
    qs = Student.objects.select_related('user')
    if classes is not None:
        qs = qs.filter(school_class__in=classes)
    return sort_students(qs, by_class=True)


def _my_classes(user):
    """Классы учителя (админа — все) с числом учеников: [('7A', 14), ...]."""
    counts = {}
    for st in _my_students(user):
        counts[st.school_class] = counts.get(st.school_class, 0) + 1
    from .quarters import grade_of
    return sorted(counts.items(), key=lambda kv: (int(grade_of(kv[0])) if str(grade_of(kv[0])).isdigit() else 99, kv[0]))


# Игры раздела: когда появятся новые, их нужно только добавить сюда — раздел «Игры» покажет карточку.
GAME_TYPES = [
    {'key': 'bomb', 'icon': '💣', 'title': 'Бомба', 'url': 'codegames_manage',
     'about': 'Обезвредить бомбу: ученик читает код и вводит, что он выведет, или перерезает провод с верным выводом.'},
]


def _int(raw, default, lo, hi):
    try:
        return min(hi, max(lo, int(raw)))
    except (TypeError, ValueError):
        return default


def _save_form(request, game):
    game.mode = 'bomb'
    topics = [t for t in request.POST.getlist('topics') if t in cg.TOPIC_LABELS]
    game.topics = ','.join(topics or list(cg.TOPIC_LABELS))
    game.questions = _int(request.POST.get('questions'), 4, 1, 12)
    game.time_limit = _int(request.POST.get('minutes'), 5, 1, 60) * 60
    game.is_active = bool(request.POST.get('is_active'))
    game.title = game.title or 'Бомба'
    mine = {c for c, _ in _my_classes(request.user)}
    chosen = {c for c in request.POST.getlist('classes') if c in mine}
    keep_foreign = [c for c in game.class_list() if c not in mine]              # чужие классы не трогаем
    game.set_classes(chosen | set(keep_foreign))
    game.save()
    game.students.clear()                                                        # доступ теперь только по классам
    auto_title(game)


def auto_title(game):
    """Название ставится само: «Бомба · 7A, 7B · if/else»."""
    classes = game.class_list()
    topics = ', '.join(cg.TOPIC_SHORT.get(t, t) for t in game.topic_list())
    game.title = ' · '.join(x for x in ['Бомба', ', '.join(classes), topics] if x)[:120]
    game.save(update_fields=['title'])


@staff_member_required
def games_manage(request):
    if request.method == 'POST':
        game = CodeGame(owner=request.user)
        _save_form(request, game)
        messages.success(request, 'Игра создана.')
        return redirect('codegame_edit', game_id=game.id)
    games = CodeGame.objects.all() if request.user.is_superuser else CodeGame.objects.filter(owner=request.user)
    return render(request, 'codegames_manage.html', {
        'games': games, 'topics': cg.TOPICS, 'classes': _my_classes(request.user), 'chosen': set(), 'game': None,
    })


@staff_member_required
def games_hub(request):
    """Раздел «Игры» для учителя: карточки всех игр платформы."""
    games = CodeGame.objects.all() if request.user.is_superuser else CodeGame.objects.filter(owner=request.user)
    cards = []
    for t in GAME_TYPES:
        mine = [g for g in games if g.mode == t['key']]
        classes = sorted({c for g in mine if g.is_active for c in g.class_list()})
        runs = CodeGameRun.objects.filter(game__in=mine).count() if mine else 0
        cards.append(dict(t, total=len(mine), active=sum(1 for g in mine if g.is_active), classes=classes, runs=runs))
    return render(request, 'games_hub.html', {'cards': cards})


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
        'game': game, 'topics': cg.TOPICS, 'classes': _my_classes(request.user),
        'chosen': set(game.class_list()), 'rows': rows,
    })


@staff_member_required
@require_POST
def game_delete(request, game_id):
    game = get_object_or_404(CodeGame, id=game_id)
    if _can_edit(request.user, game):
        game.delete()
        messages.success(request, 'Игра удалена.')
    return redirect('codegames_manage')


def _kk(request, text):
    """Сообщение ученику на его языке (RU/KZ из шапки сайта)."""
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    return UI_TRANSLATIONS.get(text, text) if request.session.get('content_lang') == 'kk' else text


def _student(request):
    st = Student.objects.filter(user=request.user).first()
    if st is None:
        raise Http404
    return st


@login_required
def games(request):
    if request.user.is_staff:
        return redirect('games_hub')
    st = _student(request)
    my = CodeGame.open_for(st)
    from .coins import balance
    items = []
    for g in my:
        won = [r.duration() for r in g.runs.filter(student=st, status='won')]
        items.append({'game': g, 'best': min(won) if won else None, 'wins': len(won),
                      'topics': [cg.TOPIC_LABELS.get(t, t) for t in g.topic_list()]})
    return render(request, 'codegames.html', {'items': items, 'fee': cg.ENTRY_FEE, 'payout': cg.PAYOUT, 'balance': balance(st)})


@login_required
@require_POST
def game_start(request, game_id):
    st = _student(request)
    game = get_object_or_404(CodeGame.open_for(st), id=game_id)
    now = timezone.now()
    current = CodeGameRun.objects.filter(game=game, student=st, status='playing').first()
    if current and not cg.expire_if_needed(current, now):
        return redirect('codegame_play', run_id=current.id)
    from .coins import balance, spend
    stake = cg.ENTRY_FEE
    if stake and balance(st) < stake:
        messages.error(request, _kk(request, 'Вход в игру стоит 1 монету — у тебя не хватает. Реши задачу, чтобы заработать.'))
        return redirect('codegames')
    questions = cg.generate(game, random.Random())
    if not questions:
        messages.error(request, _kk(request, 'Не получилось собрать вопросы. Сообщите учителю.'))
        return redirect('codegames')
    run = CodeGameRun.objects.create(game=game, student=st, questions=questions, stake=stake, started_at=timezone.now())
    if stake and not spend(st, f'gamefee:{run.id}', stake, 'Вход в игру «Бомба»'):
        run.delete()
        messages.error(request, _kk(request, 'Не хватает монет для входа в игру.'))
        return redirect('codegames')
    return redirect('codegame_play', run_id=run.id)


@login_required
def game_play(request, run_id):
    st = _student(request)
    run = get_object_or_404(CodeGameRun.objects.select_related('game'), id=run_id, student=st)
    cg.expire_if_needed(run)
    if run.status == 'playing' and run.client and run.client != _client(request):
        run = cg.forfeit(run)            # игру открыли в другом браузере — это выход из игры
        messages.error(request, _kk(request, 'Игру открыли в другом браузере — засчитан проигрыш.'))
    qs = [cg.public_question(q, i) for i, q in enumerate(run.questions)]
    return render(request, 'codegame_play.html', {
        'run': run, 'game': run.game, 'questions': qs, 'left': cg.seconds_left(run) if run.status == 'playing' else 0,
        'penalty': cg.ERROR_PENALTY, 'duration': run.duration(), 'fee': cg.ENTRY_FEE, 'payout': cg.PAYOUT,
    })


@login_required
@require_POST
def game_answer(request, run_id):
    st = _student(request)
    run = get_object_or_404(CodeGameRun, id=run_id, student=st)
    if run.status == 'playing' and run.client != _client(request):
        if run.client:                    # ответ из другого браузера — проигрыш
            run = cg.forfeit(run)
        return JsonResponse({'ok': False, 'state': run.status, 'left': cg.seconds_left(run) if run.status == 'playing' else 0,
                             'strikes': run.mistakes, 'need_fullscreen': True})
    try:
        index = int(request.POST.get('index', '-1'))
    except ValueError:
        index = -1
    return JsonResponse(cg.answer(run, index, request.POST.get('answer', '')))


def _client(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


@login_required
@require_POST
def game_arm(request, run_id):
    """Браузер вошёл в полноэкранный режим: игра привязывается к нему. Второй браузер привязаться не может."""
    st = _student(request)
    run = get_object_or_404(CodeGameRun, id=run_id, student=st)
    me = _client(request)
    updated = CodeGameRun.objects.filter(pk=run.pk, status='playing', client='').update(client=me)
    run.refresh_from_db()
    ok = run.status == 'playing' and run.client == me
    if not ok and run.status == 'playing':
        run = cg.forfeit(run)
    return JsonResponse({'ok': ok, 'state': run.status, 'bound': bool(updated)})


@login_required
@require_POST
def game_forfeit(request, run_id):
    """Вызывается из браузера, когда ученик вышел из полноэкранного режима и не вернулся: проигрыш."""
    st = _student(request)
    run = get_object_or_404(CodeGameRun, id=run_id, student=st)
    run = cg.forfeit(run)
    return JsonResponse({'state': run.status})
