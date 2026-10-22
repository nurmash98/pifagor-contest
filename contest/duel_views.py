# -*- coding: utf-8 -*-
"""Страницы дуэлей: главная (рейтинг, очередь, история), сама дуэль, отправка кода."""
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import duels
from .exam_views import active_attempt_for
from .models import Duel, DuelAttempt, DuelQueueEntry, DuelTask, SiteSettings, Student, Tag
from .models import normalize_school_class
from .badge_art import solved_counts
from .views import _apply_task_language, _get_content_lang, _managed_classes


def _student(request):
    return Student.objects.filter(user=request.user).first()


def _manageable_classes(user):
    """Классы, которым преподаватель может открыть дуэли: свои; админу — все существующие."""
    classes = _managed_classes(user)
    if classes is None:
        classes = sorted({c for c in Student.objects.values_list('school_class', flat=True) if c})
    return sorted(set(classes))


def _class_rows(user):
    obj = SiteSettings.get()
    opened = set(obj.duel_class_list())
    all_tags = list(Tag.objects.order_by('name'))
    rows = []
    for name in _manageable_classes(user):
        chosen = set((obj.duels_class_tags or {}).get(name) or [])
        rows.append({'name': name, 'open': name in opened,
                     'students': Student.objects.filter(school_class=name).count(),
                     'tag_count': len(chosen),
                     'tag_options': [(t, t.id in chosen) for t in all_tags]})
    return rows


@login_required
def duels_home(request):
    student = _student(request)
    if student is not None and not duels.visible_to(student):
        return redirect('all_tasks')          # дуэли скрыты от этого ученика
    enabled = duels.duels_enabled()
    ctx = {'enabled': enabled, 'student': student, 'readonly': student is None}
    top = Student.objects.filter(duel_games__gt=0).order_by('-duel_rating', '-duel_games', 'full_name')[:20]
    ctx['top'] = list(top)
    counts = solved_counts([s.id for s in ctx['top']])
    for s in ctx['top']:
        s.solved = counts.get(s.id, 0)
    if student is None and request.user.is_staff:
        # Обзор для преподавателя: режим, тестеры, идущие и недавние дуэли (только просмотр)
        ctx.update({
            'mode': duels.duels_mode(),
            'class_rows': _class_rows(request.user),
            'testers': list(Student.objects.filter(duel_tester=True).order_by('school_class', 'full_name')),
            'active_duels': list(Duel.objects.filter(status='ACTIVE').select_related('player1', 'player2')),
            'recent_duels': list(Duel.objects.filter(status='FINISHED').select_related('player1', 'player2', 'winner')
                                 .order_by('-finished_at')[:10]),
        })
    if student is not None:
        active = duels.active_duel_for(student)
        if active is not None:
            return redirect('duel_match', duel_id=active.id)
        allowed, reason = duels.can_play(student)
        history = list(Duel.objects.filter(Q(player1=student) | Q(player2=student), status='FINISHED')
                       .select_related('player1', 'player2', 'winner').order_by('-finished_at')[:10])
        for d in history:
            d.opponent = d.opponent_of(student)
            d.change = d.rating_change_of(student)
            d.result = 'draw' if d.winner_id is None else ('win' if d.winner_id == student.id else 'loss')
        better = Student.objects.filter(duel_games__gt=0, duel_rating__gt=student.duel_rating).count()
        ctx.update({
            'can_play': allowed, 'cant_reason': reason, 'history': history,
            'in_queue': DuelQueueEntry.objects.filter(student=student).exists(),
            'place': better + 1 if student.duel_games else None,
        })
    return render(request, 'duels_home.html', ctx)


@login_required
@require_POST
def duels_find(request):
    student = _student(request)
    if student is None:
        return redirect('duels')
    if not duels.visible_to(student):
        return redirect('all_tasks')
    if active_attempt_for(request.user):
        messages.warning(request, 'Во время СОР/СОЧ дуэли недоступны.')
        return redirect('duels')
    allowed, reason = duels.can_play(student)
    if not allowed:
        messages.warning(request, reason)
        return redirect('duels')
    active = duels.active_duel_for(student)
    if active is not None:
        return redirect('duel_match', duel_id=active.id)
    duels.join_queue(student)
    duel = duels.try_match(student)
    if duel is not None:
        return redirect('duel_match', duel_id=duel.id)
    return redirect('duels')


@login_required
@require_POST
def duels_leave(request):
    student = _student(request)
    if student is not None:
        duels.leave_queue(student)
    return redirect('duels')


@login_required
def duels_status(request):
    """Опрос из браузера пока игрок ждёт соперника: подбирает пару и сообщает, началась ли дуэль."""
    student = _student(request)
    if student is None or not duels.visible_to(student):
        return JsonResponse({'state': 'idle'})
    active = duels.active_duel_for(student)
    if active is None and DuelQueueEntry.objects.filter(student=student).exists():
        active = duels.try_match(student)
    if active is not None:
        return JsonResponse({'state': 'active', 'url': f'/duels/{active.id}/'})
    waiting = DuelQueueEntry.objects.filter(student=student).exists()
    return JsonResponse({'state': 'waiting' if waiting else 'idle'})


def _duel_context(duel, student, lang):
    """Задачи дуэли по порядку. Открыта одна (current); закрытые показываем кратко;
    будущие скрыты — их условия ученик не видит, пока не дойдёт очередь."""
    tasks = []
    current = duels.current_task(duel) if duel.status == 'ACTIVE' else None
    for dt in duel.duel_tasks.select_related('task', 'winner').prefetch_related('attempts'):
        _apply_task_language([dt.task], lang)
        mine = [a for a in dt.attempts.all() if a.player_id == student.id]
        dt.my_attempts = mine
        dt.my_left = duels.attempts_left(dt, student)
        dt.opp_left = duels.attempts_left(dt, duel.opponent_of(student))
        dt.closed = duels.task_closed(dt, duel)
        dt.mine_won = dt.winner_id == student.id
        dt.opp_won = bool(dt.winner_id) and dt.winner_id != student.id
        dt.last_code = mine[-1].code if mine else ''
        dt.is_current = current is not None and dt.id == current.id
        dt.is_future = duel.status == 'ACTIVE' and not dt.closed and not dt.is_current
        dt.seconds_left = duels.seconds_left(dt) if dt.is_current else None
        dt.timed_out_flag = dt.timed_out and not dt.winner_id
        tasks.append(dt)
    return tasks


@login_required
def duel_match(request, duel_id):
    duel = get_object_or_404(Duel.objects.select_related('player1', 'player2', 'winner'), id=duel_id)
    student = _student(request)
    if student is None or not duel.has_player(student):
        if request.user.is_staff:
            return redirect('duels')
        messages.error(request, 'Это не ваша дуэль.')
        return redirect('duels')
    duels.close_if_inactive(duel)
    duel.refresh_from_db()
    lang = _get_content_lang(request)
    me, opp = student, duel.opponent_of(student)
    ctx = {
        'me_solved': solved_counts([me.id]).get(me.id, 0), 'opp_solved': solved_counts([opp.id]).get(opp.id, 0),
        'duel': duel, 'me': me, 'opp': opp, 'tasks': _duel_context(duel, student, lang),
        'my_points': duel.points_of(me), 'opp_points': duel.points_of(opp),
        'finished': duel.status == 'FINISHED',
        'result': None if duel.status != 'FINISHED' else (
            'draw' if duel.winner_id is None else ('win' if duel.winner_id == me.id else 'loss')),
        'my_change': duel.rating_change_of(me),
        'my_rating_before': duel.p1_rating_before if me.id == duel.player1_id else duel.p2_rating_before,
        'opp_rating_before': duel.p2_rating_before if me.id == duel.player1_id else duel.p1_rating_before,
        'enabled': duels.duels_enabled(),
        'points_to_win': duels.POINTS_TO_WIN, 'attempts_per_task': duels.ATTEMPTS_PER_TASK,
    }
    ctx['signature'] = '{}-{}-{}'.format(duel.status, duel.p1_points + duel.p2_points,
                                          ''.join('1' if t.closed else '0' for t in ctx['tasks']))
    return render(request, 'duel_match.html', ctx)


@login_required
def duel_state(request, duel_id):
    """Короткий снимок для опроса страницы дуэли: если изменился — страница перезагружается."""
    duel = get_object_or_404(Duel, id=duel_id)
    student = _student(request)
    if student is None or not duel.has_player(student):
        return JsonResponse({'signature': ''}, status=403)
    duels.close_if_inactive(duel)
    duel.refresh_from_db()
    closed = ''.join('1' if duels.task_closed(dt, duel) else '0' for dt in duel.duel_tasks.all())
    return JsonResponse({'signature': f'{duel.status}-{duel.p1_points + duel.p2_points}-{closed}'})


@login_required
@require_POST
def duel_submit(request, duel_id, duel_task_id):
    duel = get_object_or_404(Duel, id=duel_id)
    student = _student(request)
    if student is None or not duel.has_player(student):
        messages.error(request, 'Это не ваша дуэль.')
        return redirect('duels')
    if not duels.duels_enabled():
        messages.warning(request, 'Дуэли сейчас выключены преподавателем.')
        return redirect('duel_match', duel_id=duel.id)
    duel_task = get_object_or_404(DuelTask, id=duel_task_id, duel=duel)
    ok, msg, _result = duels.submit_code(duel, student, duel_task, request.POST.get('code', ''))
    (messages.success if ok and 'Верно' in msg else messages.info if ok else messages.warning)(request, msg)
    return redirect(f'/duels/{duel.id}/#task-{duel_task.id}')


@login_required
@require_POST
def duel_resign(request, duel_id):
    duel = get_object_or_404(Duel, id=duel_id)
    student = _student(request)
    if student is not None and duel.has_player(student):
        duels.resign(duel, student)
    return redirect('duel_match', duel_id=duel.id)


@login_required
@require_POST
def duel_leave(request, duel_id):
    """Вызывается из браузера (sendBeacon), когда ученик ушёл со страницы дуэли: сразу поражение."""
    duel = get_object_or_404(Duel, id=duel_id)
    student = _student(request)
    if student is not None and duel.has_player(student):
        duels.resign(duel, student, reason='left')
    return JsonResponse({'ok': True})


@staff_member_required
@require_POST
def duels_toggle(request):
    """Переключает режим дуэлей по кругу: выключены → по классам и тестерам → для всех."""
    if not duels.is_duels_admin(request.user):
        messages.error(request, 'Управлять дуэлями может только администратор дуэлей.')
        return redirect('teacher_dashboard')
    order = ['off', 'testers', 'all']
    settings_obj = SiteSettings.get()
    settings_obj.duels_mode = order[(order.index(settings_obj.duels_mode) + 1) % len(order)]
    settings_obj.save()
    labels = {'off': 'Дуэли выключены.', 'testers': 'Дуэли видят только открытые классы и тестеры.', 'all': 'Дуэли открыты для всех.'}
    messages.success(request, labels[settings_obj.duels_mode])
    return redirect(request.POST.get('next') or 'teacher_dashboard')


@staff_member_required
@require_POST
def duels_class_toggle(request):
    """Преподаватель открывает или закрывает дуэли одному из своих классов."""
    name = normalize_school_class(request.POST.get('school_class', ''))
    if name not in _manageable_classes(request.user):
        messages.error(request, 'Можно управлять дуэлями только своих классов.')
        return redirect('duels')
    obj = SiteSettings.get()
    current = obj.duel_class_list()
    if request.POST.get('action') == 'close':
        current = [c for c in current if c != name]
        messages.success(request, f'Дуэли для класса {name} закрыты.')
    else:
        current.append(name)
        messages.success(request, f'Дуэли для класса {name} открыты.')
    obj.set_duel_classes(current)
    obj.save(update_fields=['duels_classes'])
    return redirect('duels')


@staff_member_required
@require_POST
def duels_class_tags(request):
    """Преподаватель выбирает, задачи каких тегов выпадают в дуэлях его класса (пусто — любые)."""
    name = normalize_school_class(request.POST.get('school_class', ''))
    if name not in _manageable_classes(request.user):
        messages.error(request, 'Можно управлять дуэлями только своих классов.')
        return redirect('duels')
    valid = set(Tag.objects.values_list('id', flat=True))
    chosen = sorted({int(t) for t in request.POST.getlist('tags') if t.isdigit() and int(t) in valid})
    obj = SiteSettings.get()
    data = dict(obj.duels_class_tags or {})
    if chosen:
        data[name] = chosen
        messages.success(request, f'Класс {name}: в дуэлях будут задачи выбранных тегов ({len(chosen)}).')
    else:
        data.pop(name, None)
        messages.success(request, f'Класс {name}: в дуэлях будут задачи любых тегов.')
    obj.duels_class_tags = data
    obj.save(update_fields=['duels_class_tags'])
    return redirect('duels')
