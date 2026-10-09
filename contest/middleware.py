# -*- coding: utf-8 -*-
from django.contrib import messages
from django.shortcuts import redirect

# Страницы, закрытые для ученика, пока он пишет СОР/СОЧ.
EXAM_LOCKED_VIEWS = {
    'all_tasks', 'task_detail',
    'courses', 'course_detail', 'topic_video', 'topic_theory',
}


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
        attempt = getattr(request, 'active_exam_attempt', None)
        match = getattr(request, 'resolver_match', None)
        if attempt is not None and match is not None and match.url_name in EXAM_LOCKED_VIEWS:
            messages.warning(request, 'Идёт СОР/СОЧ (или пересдача) — Курсы и Все задачи откроются после его окончания.')
            return redirect(attempt.get_absolute_url())
        return None
