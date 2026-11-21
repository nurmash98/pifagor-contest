# -*- coding: utf-8 -*-
"""«Офис выходного дня» (сб/вс 9:00–20:00, без учителя): страница, опрос, действия, язык."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import office as of
from . import room as rm
from .models import Student, Task
from .room_views import COLORS, _label, _lang, _texts


def _who(request):
    """(student, grade, teacher?) — ученики 5–11 классов и учителя (смотрят)."""
    if request.user.is_staff:
        return None, None, True
    key = f'office:who:{request.user.id}'
    got = cache.get(key)
    if got is None:
        from .quarters import grade_of
        st = Student.objects.filter(user=request.user).first()
        g = grade_of(st.school_class) if st else ''
        got = (st, g if g.isdigit() and int(g) >= 5 else None)
        cache.set(key, got, 300)
    return got[0], got[1], False


def _base(request, st, grade, teacher):
    def make():
        if teacher:
            return {'n': request.user.get_full_name() or request.user.username, 'teacher': True, 'c': -1, 'g': of.ADMIN,
                    'x': of.SPAWN[0], 'y': of.SPAWN[1], 'j': timezone.now().timestamp()}
        return dict({'n': st.full_name, 'teacher': False, 'c': st.id % len(COLORS), 'sid': st.id, 'g': grade,
                     'x': of.SPAWN[0] + request.user.id % 8, 'y': of.SPAWN[1], 'j': timezone.now().timestamp()}, **_label(st))
    return make


def _office_texts(lang):
    """Тексты зала + свои для офиса (заставка полного экрана без «урока» и «учителя»)."""
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    t = _texts(lang)
    tr = (lambda v: UI_TRANSLATIONS.get(v, v)) if lang == 'kk' else (lambda v: v)
    t['fs_title'] = tr('Офис выходного дня')
    t['gate_text'] = tr('Офис работает в полноэкранном режиме — так удобнее решать задачи.')
    return t


def _tasks(lang):
    key = f'office:tasks:{lang}'
    out = cache.get(key)
    if out is None:
        out, tags = [], {}
        for t in Task.objects.filter(hidden=False).prefetch_related('tags').order_by('level', 'id'):
            ids = []
            for g in t.tags.all():
                tags[g.id] = g.name
                ids.append(g.id)
            out.append({'id': t.id, 'title': t.get_display_title(lang), 'level': t.level, 'url': reverse('task_detail', args=[t.id]),
                        'pay': rm.PAY.get(t.level, rm.PAY['A']), 'tags': ids})
        out = (out, sorted(tags.items(), key=lambda kv: kv[1].lower()))
        cache.set(key, out, 300)
    return out


@login_required
def office_play(request):
    st, grade, teacher = _who(request)
    if not teacher and grade is None:
        messages.info(request, 'Офис выходного дня — для учеников 5–11 классов.')
        return redirect('codegames')
    if not of.is_open() and not teacher:
        messages.info(request, 'Офис открыт по субботам и воскресеньям с 9:00 до 20:00.')
        return redirect('codegames')
    day = timezone.localdate()
    r = of.rid(day)
    rm.touch(r, request.user.id, _base(request, st, grade, teacher))
    if st is not None:
        rm.touch(r, request.user.id, {}, **_label(st))
    lang = _lang(request)
    tasks, tags = _tasks(lang)
    from .models import Submission
    solved = list(Submission.objects.filter(student=st, is_graded=True, score=10).values_list('task_id', flat=True)) if st else []
    room = type('R', (), {'id': 0, 'school_class': '', 'title': ''})()
    return render(request, 'room_play.html', {
        'room': room, 'teacher': teacher, 'me': request.user.id, 'office': True, 'my_grade': of.ADMIN if teacher else (grade or ''),
        'state_url': reverse('office_state'), 'act_url': reverse('office_act'), 'lang_url': reverse('office_lang'),
        'layout': json.dumps(of.layout(of.grades())), 'tasks_json': json.dumps(tasks, ensure_ascii=False),
        'tags_json': json.dumps(tags, ensure_ascii=False), 'solved_json': json.dumps(solved), 'grades_json': json.dumps(of.grades()),
        'colors': json.dumps(COLORS), 'say_max': rm.SAY_MAX, 'theory': '[]', 'texts': json.dumps(_office_texts(lang), ensure_ascii=False),
    })


def _class_names(grade):
    key = f'office:names:{grade}'
    names = cache.get(key)
    if names is None:
        from .quarters import grade_of
        names = {sid: (uid, n) for sid, uid, n, c in Student.objects.values_list('id', 'user_id', 'full_name', 'school_class')
                 if grade_of(c) == grade}
        cache.set(key, names, 300)
    return names


@login_required
def office_state(request):
    st, grade, teacher = _who(request)
    day = timezone.localdate()
    r = of.rid(day)
    open_ = of.is_open()
    if open_ and (teacher or grade):
        rm.touch(r, request.user.id, _base(request, st, grade, teacher))
    ppl = rm.public(r)
    if open_:
        of.collect(day)
    data = of.stats(day)
    online = {p[0] for p in ppl}
    show = grade if not teacher else (request.GET.get('g') or (of.grades() or [''])[0])
    names = _class_names(show)
    rows = [[names[sid][0], names[sid][1], m, n, names[sid][0] in online, 0, None] for sid, m, n in data.get(show, []) if sid in names]
    out = {'open': open_, 'people': ppl, 'stats': rows[:15],
           'left': max(0, int((of.closes_at() - timezone.now()).total_seconds())) if open_ else None}
    from .room_views import _dm_state
    _dm_state(request, r, out)
    if st is not None:
        key = f'office:me:{day}:{st.id}'
        mine = None if request.GET.get('fresh') == '1' else cache.get(key)
        if mine is None:
            from .models import OfficeSolve
            mine = list(OfficeSolve.objects.filter(student=st, day=day).values_list('task_id', 'money', 'coins'))
            cache.set(key, mine, 15)
        out['ome'] = {'money': sum(m for _, m, _ in mine), 'coins': sum(c for _, _, c in mine), 'today': [t for t, _, _ in mine],
                      'solved': [t for t, _, _ in mine]}
    return JsonResponse(out)


@login_required
@require_POST
def office_act(request):
    st, grade, teacher = _who(request)
    if not of.is_open():
        return JsonResponse({'error': 'closed'})
    if not teacher and grade is None:
        return JsonResponse({'error': 'bad'})
    r = of.rid(timezone.localdate())
    me = request.user.id
    base = _base(request, st, grade, teacher)
    act = request.POST.get('a')
    if act == 'move':
        try:
            x = max(0.0, min(80.0, float(request.POST.get('x'))))
            y = max(0.0, min(40.0, float(request.POST.get('y'))))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        seat = (request.POST.get('seat') or '')[:8] or None
        if seat and of.seat_grade(seat) != (of.ADMIN if teacher else grade):
            return JsonResponse({'error': 'admin_only' if of.seat_grade(seat) == of.ADMIN else 'other_grade'})
        if seat and rm.seat_taken(r, seat, me):
            return JsonResponse({'error': 'taken'})
        rm.touch(r, me, base, x=round(x, 2), y=round(y, 2), seat=seat)
        return JsonResponse({'ok': True})
    if act == 'dm':
        rm.touch(r, me, base)
        err = rm.dm_send(r, me, request.POST.get('to') or 0, request.POST.get('text'))
        return JsonResponse({'error': err} if err else {'ok': True, 'conv': rm.dm_get(r, me, request.POST.get('to'))})
    if act == 'say':
        rm.touch(r, me, base)
        err = rm.say(r, me, request.POST.get('text'))
        return JsonResponse({'error': err} if err else {'ok': True})
    if act == 'hand':
        p = rm.touch(r, me, base)
        rm.touch(r, me, base, hand=not p.get('hand'))
        return JsonResponse({'ok': True})
    if act == 'exit':
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'bad'})


@login_required
@require_POST
def office_lang(request):
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    lang = request.POST.get('lang')
    if lang not in ('ru', 'kk'):
        return JsonResponse({'ok': False})
    request.session['content_lang'] = lang
    try:
        keys = [k for k in json.loads(request.POST.get('keys') or '[]') if isinstance(k, str)][:200]
    except ValueError:
        keys = []
    tasks, _ = _tasks(lang)
    return JsonResponse({'ok': True, 'txt': _office_texts(lang), 'tasks': tasks,
                         'tr': {k: (UI_TRANSLATIONS.get(k, k) if lang == 'kk' else k) for k in keys}})
