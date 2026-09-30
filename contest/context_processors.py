def content_lang(request):
    """Текущий язык отображения условий задач ('ru' или 'kk'), выбранный
    переключателем в шапке сайта и сохранённый в сессии пользователя."""
    lang = request.session.get('content_lang', 'ru') if hasattr(request, 'session') else 'ru'
    if lang not in ('ru', 'kk'):
        lang = 'ru'
    return {'content_lang': lang}


def active_exam(request):
    """Идущий у ученика СОР/СОЧ (ставит contest.middleware.ExamLockMiddleware) — для меню."""
    return {'active_exam_attempt': getattr(request, 'active_exam_attempt', None)}
