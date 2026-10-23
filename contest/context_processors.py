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
    """Показывать ли пункт «Дуэли» в меню: ученику — в режиме тестеров/для всех (если он тестер),
    преподавателю — админу дуэлей всегда, остальным только когда дуэли открыты для всех."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'duels_visible': False, 'duels_admin': False}
    from .duels import is_duels_admin, teacher_sees_duels, visible_to
    from .models import Student
    if user.is_staff:
        return {'duels_visible': teacher_sees_duels(user), 'duels_admin': is_duels_admin(user)}
    student = Student.objects.filter(user=user).first()
    return {'duels_visible': student is not None and visible_to(student), 'duels_admin': False}


def holiday(request):
    """Праздничное оформление с флагом Казахстана: День Республики — 25 октября (показываем 23–27 октября),
    День Независимости — 16 декабря (14–18 декабря). Для проверки заранее: HOLIDAY_FORCE=republic / independence."""
    from django.conf import settings
    from django.utils import timezone
    forced = getattr(settings, 'HOLIDAY_FORCE', '')
    if forced:
        return {'holiday': forced}
    today = timezone.localdate()
    if today.month == 10 and 23 <= today.day <= 27:
        return {'holiday': 'republic'}
    if today.month == 12 and 14 <= today.day <= 18:
        return {'holiday': 'independence'}
    return {'holiday': ''}


def coins(request):
    """Баланс монет ученика для шапки сайта."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or user.is_staff:
        return {'coin_balance': None, 'my_solved': 0}
    from .coins import balance
    from .models import Student, Submission
    student = Student.objects.filter(user=user).first()
    if not student:
        return {'coin_balance': None, 'my_solved': 0}
    solved = Submission.objects.filter(student=student, is_graded=True, score=10).count()
    return {'coin_balance': balance(student), 'my_solved': solved}
