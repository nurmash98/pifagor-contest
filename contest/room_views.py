# -*- coding: utf-8 -*-
"""3D-коворкинг класса: учитель открывает зал на урок и выбирает задачи, ученики садятся за столы и решают."""
import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import room as rm
from .models import Student, StudyRoom, Task

COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6', '#3b82f6', '#6366f1', '#a855f7', '#ec4899', '#0ea5e9', '#84cc16', '#f43f5e']


def _lang(request):
    return 'kk' if request.session.get('content_lang') == 'kk' else 'ru'


def open_room_for_class(cls):
    """Открытый зал класса (кеш 30 секунд — для полосы-приглашения на каждой странице)."""
    if not cls:
        return None
    key = f'room:open:{cls}'
    rid = cache.get(key)
    if rid is None:
        now = timezone.now()
        r = (StudyRoom.objects.filter(school_class=cls, closed_at__isnull=True)
             .filter(ends_at__isnull=True).first() or
             StudyRoom.objects.filter(school_class=cls, closed_at__isnull=True, ends_at__gt=now).first())
        rid = r.id if r else 0
        cache.set(key, rid, 30)
    return rid or None


def _forget_open(cls):
    cache.delete(f'room:open:{cls}')


def _can_teach(user, room):
    if user.is_superuser or room.owner_id == user.id:
        return True
    from .views import _managed_classes
    scope = _managed_classes(user)
    return scope is None or room.school_class in scope


def _access(request, room_id):
    """(room, student, teacher?) или Http404."""
    room = get_object_or_404(StudyRoom, id=room_id)
    if request.user.is_staff:
        if not _can_teach(request.user, room):
            raise Http404
        return room, None, True
    st = Student.objects.filter(user=request.user).first()
    if st is None or st.school_class != room.school_class:
        raise Http404
    return room, st, False


def _base(request, st, teacher):
    if teacher:
        name = request.user.get_full_name() or request.user.username
        return {'n': name, 'teacher': True, 'c': -1, 'x': 10, 'y': 2, 'seat': 't'}      # учитель сразу за своим столом
    return {'n': st.full_name or request.user.username, 'teacher': False, 'c': st.id % len(COLORS),
            'x': 2 + request.user.id % 15, 'y': 5}                                      # входят в проход перед рядами


# ------------------------------------------------------------------ учитель

@staff_member_required
def room_manage(request):
    from .codegame_views import _my_classes
    from .scratch_levels import is_junior_class
    classes = [(c, n) for c, n in _my_classes(request.user) if not is_junior_class(c)]
    mine = {c for c, _ in classes}
    if request.method == 'POST':
        cls = request.POST.get('school_class')
        if cls not in mine:
            messages.error(request, 'Выберите свой класс.')
            return redirect('room_manage')
        ids = [int(x) for x in request.POST.getlist('tasks') if x.isdigit()]
        tasks = list(Task.objects.filter(id__in=ids, hidden=False))
        if not tasks:
            messages.error(request, 'Выберите хотя бы одну задачу на урок.')
            return redirect('room_manage')
        try:
            minutes = max(5, min(240, int(request.POST.get('minutes') or 45)))
        except ValueError:
            minutes = 45
        now = timezone.now()
        StudyRoom.objects.filter(school_class=cls, closed_at__isnull=True).update(closed_at=now)   # один зал на класс
        room = StudyRoom.objects.create(school_class=cls, owner=request.user, title=(request.POST.get('title') or '').strip()[:120],
                                        ends_at=now + timedelta(minutes=minutes))
        room.tasks.set(tasks)
        _forget_open(cls)
        return redirect('room_play', room_id=room.id)
    now = timezone.now()
    rooms = list(StudyRoom.objects.filter(school_class__in=mine).prefetch_related('tasks')[:30])
    for r in rooms:
        r.open_now = r.is_open(now)
        r.online = len(rm.people(r.id)) if r.open_now else 0
    tasks = list(Task.objects.filter(hidden=False).prefetch_related('tags').order_by('level', 'title'))
    return render(request, 'room_manage.html', {'classes': classes, 'rooms': rooms, 'tasks': tasks})


@staff_member_required
@require_POST
def room_close(request, room_id):
    room = get_object_or_404(StudyRoom, id=room_id)
    if not _can_teach(request.user, room):
        raise Http404
    if room.closed_at is None:
        room.closed_at = timezone.now()
        room.save(update_fields=['closed_at'])
    _forget_open(room.school_class)
    messages.success(request, f'Коворкинг {room.school_class} закрыт.')
    return redirect('room_manage')


# ------------------------------------------------------------------ зал

@login_required
def room_enter(request):
    """Ученик: войти в открытый зал своего класса."""
    st = Student.objects.filter(user=request.user).first()
    rid = open_room_for_class(st.school_class if st else None)
    if request.user.is_staff:
        return redirect('room_manage')
    if not rid:
        messages.info(request, 'Учитель пока не открыл коворкинг для вашего класса.')
        return redirect('codegames')
    return redirect('room_play', room_id=rid)


ROOM_TEXTS = {
    'sit_first': 'Сначала сядьте за стол', 'taken': 'Это место занято', 'wait': 'Не так быстро — подождите пару секунд',
    'teacher_seat': 'Это стол учителя', 'you': 'вы', 'hand_up': 'Опустить руку', 'hand': 'Поднять руку', 'lower': 'опустить',
    'none': '—', 'of': 'из', 'at_desk': 'Вы за столом — выбирайте задачу справа', 'close_q': 'Закрыть зал для всего класса?',
    'fs_title': 'Урок в коворкинге', 'fs_start': 'Войти в полноэкранном режиме', 'fs_out': 'Вы вышли из полноэкранного режима',
    'fs_back': 'Вернуться в полноэкранный режим', 'exits': 'Выходов из полного экрана / на другие вкладки',
}


def _texts(lang):
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    return {k: (UI_TRANSLATIONS.get(v, v) if lang == 'kk' else v) for k, v in ROOM_TEXTS.items()}


def _task_list(room, lang):
    return [{'id': t.id, 'title': t.get_display_title(lang), 'level': t.level, 'url': reverse('task_detail', args=[t.id])}
            for t in room.tasks.order_by('level', 'id')]


@login_required
@require_POST
def room_lang(request, room_id):
    """RU/KZ прямо в зале, без перезагрузки (иначе сбросился бы полный экран)."""
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    room, st, teacher = _access(request, room_id)
    lang = request.POST.get('lang')
    if lang not in ('ru', 'kk'):
        return JsonResponse({'ok': False})
    request.session['content_lang'] = lang
    try:
        keys = [k for k in json.loads(request.POST.get('keys') or '[]') if isinstance(k, str)][:200]
    except ValueError:
        keys = []
    return JsonResponse({'ok': True, 'txt': _texts(lang), 'tasks': _task_list(room, lang),
                         'tr': {k: (UI_TRANSLATIONS.get(k, k) if lang == 'kk' else k) for k in keys}})


@login_required
def room_play(request, room_id):
    room, st, teacher = _access(request, room_id)
    if not room.is_open() and not teacher:
        messages.info(request, 'Этот коворкинг уже закрыт.')
        return redirect('codegames')
    lang = _lang(request)
    n = Student.objects.filter(school_class=room.school_class).count()
    tasks = _task_list(room, lang)
    me = request.user.id
    rm.touch(room.id, me, _base(request, st, teacher))
    return render(request, 'room_play.html', {
        'room': room, 'teacher': teacher, 'me': me,
        'layout': json.dumps(rm.layout(n)), 'tasks_json': json.dumps(tasks, ensure_ascii=False),
        'colors': json.dumps(COLORS), 'say_max': rm.SAY_MAX, 'texts': json.dumps(_texts(lang), ensure_ascii=False),
    })


def _stats_rows(room, data, online, exits=None):
    rows = []
    exits = exits or {}
    for s in Student.objects.filter(school_class=room.school_class).only('id', 'full_name', 'user_id').order_by('full_name'):
        rows.append([s.user_id, s.full_name, len(data['now'].get(s.id, [])), len(data['before'].get(s.id, [])), s.user_id in online,
                     exits.get(s.user_id, 0)])
    rows.sort(key=lambda r: (-r[2], -r[3], r[1]))
    return rows


@login_required
def room_state(request, room_id):
    room, st, teacher = _access(request, room_id)
    me = request.user.id
    fresh = request.GET.get('fresh') == '1' and cache.add(f'room:fresh:{me}', 1, 3)
    if room.is_open():
        rm.touch(room.id, me, _base(request, st, teacher))
    ppl = rm.public(room.id)
    data = rm.stats(room, fresh=fresh)
    online = {p[0] for p in ppl}
    out = {'open': room.is_open(), 'people': ppl,
           'left': max(0, int((room.ends_at - timezone.now()).total_seconds())) if room.ends_at else None,
           'stats': _stats_rows(room, data, online, rm.exits(room.id) if teacher else None), 'n_tasks': room.tasks.count()}
    if st is not None:
        out['mine'] = data['now'].get(st.id, []) + data['before'].get(st.id, [])
    if teacher:
        out['log'] = rm.log(room.id)[-30:]
    return JsonResponse(out)


@login_required
@require_POST
def room_act(request, room_id):
    room, st, teacher = _access(request, room_id)
    if not room.is_open():
        return JsonResponse({'error': 'closed'})
    me = request.user.id
    base = _base(request, st, teacher)
    act = request.POST.get('a')
    if act == 'move':
        try:
            x = max(0.0, min(60.0, float(request.POST.get('x'))))
            y = max(0.0, min(60.0, float(request.POST.get('y'))))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        seat = (request.POST.get('seat') or '')[:4] or None
        if seat == 't' and not teacher:
            return JsonResponse({'error': 'teacher_seat'})
        if seat and rm.seat_taken(room.id, seat, me):
            return JsonResponse({'error': 'taken'})
        rm.touch(room.id, me, base, x=round(x, 2), y=round(y, 2), seat=seat)
        return JsonResponse({'ok': True})
    if act == 'say':
        rm.touch(room.id, me, base)
        err = rm.say(room.id, me, request.POST.get('text'))
        return JsonResponse({'error': err} if err else {'ok': True})
    if act == 'hand':
        p = rm.touch(room.id, me, base)
        rm.touch(room.id, me, base, hand=not p.get('hand'))
        return JsonResponse({'ok': True})
    if act == 'exit' and not teacher:
        rm.add_exit(room.id, me)
        return JsonResponse({'ok': True})
    if act == 'lower' and teacher:
        try:
            uid = int(request.POST.get('uid'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        p = rm.get(room.id, uid)
        if p:
            rm.touch(room.id, uid, p, hand=False)
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'bad'})
