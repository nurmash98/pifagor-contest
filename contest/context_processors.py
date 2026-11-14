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
    """Показывать ли пункт «Дуэли» в меню: ученику — если его класс открыт или дуэли для всех,
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


def code_games(request):
    """Пункт «Игры» в меню: ученику — если учитель открыл ему хотя бы одну игру."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or user.is_staff:
        return {'games_visible': False}
    from .models import CodeGame, IslandGame, Student
    student = Student.objects.filter(user=user).first()
    return {'games_visible': student is not None and (CodeGame.open_for(student).exists()
                                                      or IslandGame.open_for(student).exists())}


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
    # Новые начисления (учитель поставил 10/10, награда четверти…) подтягиваем не чаще раза в минуту
    import time
    session = getattr(request, 'session', None)
    if session is not None and time.time() - session.get('coin_sync_at', 0) > 60:
        from .coins import sync
        sync(student)
        session['coin_sync_at'] = time.time()
    bal = balance(student)
    # Анимация «монеты летят в счётчик» — когда баланс вырос с прошлой страницы
    gain = 0
    if session is not None:
        seen = session.get('coin_seen')
        if seen is not None and bal > seen:
            gain = bal - seen
        if seen != bal:
            session['coin_seen'] = bal
    return {'coin_balance': bal, 'my_solved': solved, 'coin_gain': gain}


def notices(request):
    """Непрочитанное уведомление ученику (убран из рейтинга и т. п.) — показывается сверху страницы."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or user.is_staff:
        return {'student_notice': None}
    from .models import StudentNotice
    note = StudentNotice.objects.filter(student__user=user, seen_at__isnull=True).first()
    return {'student_notice': note}


def olympiads_visible(request):
    """Пункт «Олимпиада» в меню: учителю — всегда, ученику — если учитель добавил его хоть в одну олимпиаду."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'olympiads_visible': False}
    if user.is_staff:
        return {'olympiads_visible': True}
    from .models import Exam
    return {'olympiads_visible': Exam.objects.filter(kind='OLYM', participants__user=user).exists()}
