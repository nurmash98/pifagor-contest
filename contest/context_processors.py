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


def registration(request):
    """Открыта ли регистрация (settings.REGISTRATION_OPEN) — для кнопок в шапке и на входе."""
    from django.conf import settings
    return {'registration_open': settings.REGISTRATION_OPEN}


def duels_visible(request):
    """Показывать ли ученику пункт «Дуэли» в меню (режим дуэлей + он тестер)."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or user.is_staff:
        return {'duels_visible': False}
    from .duels import visible_to
    from .models import Student
    student = Student.objects.filter(user=user).first()
    return {'duels_visible': student is not None and visible_to(student)}
