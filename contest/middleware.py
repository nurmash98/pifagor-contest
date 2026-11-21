# -*- coding: utf-8 -*-
from django.contrib import messages
from django.shortcuts import redirect

# Страницы, закрытые для ученика, пока он пишет СОР/СОЧ.
EXAM_LOCKED_VIEWS = {
    'all_tasks', 'task_detail',
    'courses', 'course_detail', 'topic_video', 'topic_theory',
    'olympiad_upsolve', 'olympiad_upsolve_task', 'room_enter', 'room_play', 'room_state', 'room_act',
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
        if user is not None and user.is_authenticated and not user.is_staff:
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
