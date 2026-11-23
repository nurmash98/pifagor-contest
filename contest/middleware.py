# -*- coding: utf-8 -*-
from django.contrib import messages
from django.shortcuts import redirect

# Страницы, закрытые для ученика, пока он пишет СОР/СОЧ.
EXAM_LOCKED_VIEWS = {
    'all_tasks', 'task_detail',
    'courses', 'course_detail', 'topic_video', 'topic_theory',
    'olympiad_upsolve', 'olympiad_upsolve_task', 'room_enter', 'room_play', 'room_state', 'room_act', 'office_play', 'office_act',
}


# Страницы с задачами на Python — ученикам 1–4 классов вместо них Scratch-тренажёр.
JUNIOR_BLOCKED_VIEWS = {
    'all_tasks', 'task_detail', 'courses', 'course_detail', 'topic_video', 'topic_theory', 'subtopic_theory',
    'leaderboard', 'duels', 'olympiad_list',
}


class RealIPMiddleware:
    """За nginx на своём сервере адрес клиента приходит в заголовке X-Real-IP (nginx его перезаписывает,
    подделать нельзя). Берём его, только если запрос пришёл с самого сервера (127.0.0.1) и включено
    TRUST_X_REAL_IP — иначе ничего не меняем (Render, локальная разработка)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        if getattr(settings, 'TRUST_X_REAL_IP', False) and request.META.get('REMOTE_ADDR') in ('127.0.0.1', '::1'):
            real = (request.META.get('HTTP_X_REAL_IP') or '').strip()
            if real:
                request.META['REMOTE_ADDR'] = real
        return self.get_response(request)


class ExamLockMiddleware:
    """Пока у ученика идёт СОР/СОЧ (нажал «Начать», не завершил, время не вышло),
    Курсы и Все задачи недоступны — перекидываем обратно на экзамен.
    Идущая попытка кладётся в request.active_exam_attempt (для меню)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.active_exam_attempt = None
        user = getattr(request, 'user', None)
        polling = is_polling(request.path)            # частые опросы игр — без лишних запросов к базе
        if user is not None and not polling and user.is_authenticated and not user.is_staff:
            from .exam_views import active_attempt_for
            request.active_exam_attempt = active_attempt_for(user)
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, 'resolver_match', None)
        user = getattr(request, 'user', None)
        if match is not None and match.url_name in JUNIOR_BLOCKED_VIEWS and user is not None and user.is_authenticated and not user.is_staff:
            from .models import Student
            from .scratch_levels import is_junior
            if is_junior(Student.objects.filter(user=user).first()):
                return redirect('scratch_home')
        attempt = getattr(request, 'active_exam_attempt', None)
        match = getattr(request, 'resolver_match', None)
        if attempt is not None and match is not None and match.url_name in EXAM_LOCKED_VIEWS:
            messages.warning(request, 'Идёт СОР/СОЧ (или пересдача) — Курсы и Все задачи откроются после его окончания.')
            return redirect(attempt.get_absolute_url())
        return None


def is_polling(path):
    """Частые опросы игр (раз в 2–3 секунды): коворкинг и остров."""
    return path.endswith('/state/') and (path.startswith('/room/') or path.startswith('/office/') or path.startswith('/games/island/'))


from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402


class PollingSessionMiddleware(SessionMiddleware):
    """Как обычная сессия (SESSION_SAVE_EVERY_REQUEST продлевает её на каждом запросе), но частые опросы игр
    продлевают сессию не чаще раза в 5 минут — иначе каждый опрос писал бы в базу (Neon)."""

    def process_response(self, request, response):
        session = getattr(request, 'session', None)
        if session is not None and is_polling(request.path) and not session.modified and session.session_key:
            from django.core.cache import cache
            if not cache.add(f'sess:touch:{session.session_key}', 1, 300):
                return response
        return super().process_response(request, response)


class MovedSiteMiddleware:
    """Сайт переехал: если задан SITE_MOVED_TO (например, на старом Render —
    https://pifagor.infosabaq.kz), любой адрес отправляется туда же на ту же страницу (301)."""

    def __init__(self, get_response):
        from django.conf import settings
        self.get_response = get_response
        self.target = (getattr(settings, 'SITE_MOVED_TO', '') or '').rstrip('/')

    def __call__(self, request):
        if self.target:
            from django.http import HttpResponsePermanentRedirect
            return HttpResponsePermanentRedirect(self.target + request.get_full_path())
        return self.get_response(request)
