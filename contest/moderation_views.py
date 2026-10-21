"""Подозрительные аккаунты: список для учителя и блокировка/разблокировка ученика.

Решение считается «слишком быстрым», если первая отправка по задаче пришла не позже
FAST_SOLVE_SECONDS после открытия задачи и прошла ВСЕ автотесты (10/10). Ученик попадает в
список, когда таких задач у него не меньше FAST_SOLVE_MIN_TASKS. Система только помечает —
блокирует (is_active=False) всегда учитель."""
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Attempt, LoginEvent, Student
from .views import _managed_classes, _managed_student_or_none

BLOCK_HOURS = 72               # срок временной блокировки
FAST_SOLVE_SECONDS = 15
FAST_SOLVE_MIN_TASKS = 3


def fast_solves(students_qs):
    """{student_id: [(задача, секунд), ...]} — быстрые первые решения (10/10)."""
    result = {}
    attempts = (Attempt.objects.filter(number=1, score=10, submission__student__in=students_qs)
                .select_related('submission__task', 'submission__student')
                .order_by('submission__student_id', 'submitted_at'))
    for a in attempts:
        sub = a.submission
        seconds = (a.submitted_at - sub.created_at).total_seconds()
        if 0 <= seconds <= FAST_SOLVE_SECONDS:
            result.setdefault(sub.student_id, []).append((sub.task, round(seconds)))
    return result


AUTOMATION_MARKERS = ('headless', 'selenium', 'playwright', 'puppeteer', 'phantomjs',
                      'webdriver', 'python-requests', 'curl/', 'httpx', 'aiohttp')
NO_ACTIVITY_MIN_ATTEMPTS = 2   # отправок, при которых страницу «никто не трогал»
MANY_DEVICES_MIN = 3           # разных устройств (User-Agent) за неделю


def _reasons(students_qs):
    """{student_id: [текст причины, ...]} — всё, что показывает «заходит не сам»."""
    from datetime import timedelta
    from django.utils import timezone
    reasons = {}

    def add(student_id, text):
        reasons.setdefault(student_id, []).append(text)

    for sid, items in fast_solves(students_qs).items():
        if len(items) >= FAST_SOLVE_MIN_TASKS:
            add(sid, f'Быстрые решения: {len(items)} задач решены на 10/10 за {FAST_SOLVE_SECONDS} с или быстрее — ' +
                '; '.join(f'{t.title} ({sec} с)' for t, sec in items[:5]))

    idle = {}
    for a in (Attempt.objects.filter(key_count=0, pointer_count=0, submission__student__in=students_qs)
              .select_related('submission')):
        idle[a.submission.student_id] = idle.get(a.submission.student_id, 0) + 1
    for sid, n in idle.items():
        if n >= NO_ACTIVITY_MIN_ATTEMPTS:
            add(sid, f'Страница задачи не трогалась: {n} отправок без единого нажатия клавиш и движения мыши')

    users = {st.user_id: st.id for st in students_qs}
    now = timezone.now()
    devices, auto = {}, {}
    for ev in LoginEvent.objects.filter(user_id__in=list(users), at__gte=now - timedelta(days=30)):
        sid = users[ev.user_id]
        ua = ev.user_agent or ''
        if any(m in ua.lower() for m in AUTOMATION_MARKERS):
            auto[sid] = ua[:80]
        if ev.at >= now - timedelta(days=7):
            devices.setdefault(sid, set()).add(ua)
    for sid, ua in auto.items():
        add(sid, f'Вход из автоматизированного браузера или скрипта ({ua})')
    for sid, uas in devices.items():
        if len(uas) >= MANY_DEVICES_MIN:
            add(sid, f'За неделю вход с {len(uas)} разных устройств/браузеров')
    return reasons


@staff_member_required
def suspicious_students(request):
    release_expired_blocks()
    classes = _managed_classes(request.user)
    students = Student.objects.select_related('user')
    if classes is not None:
        students = students.filter(school_class__in=classes)
    reasons = _reasons(students)
    rows = []
    for st in students.filter(id__in=list(reasons)).order_by('school_class', 'full_name'):
        rows.append({'student': st, 'reasons': reasons[st.id], 'count': len(reasons[st.id]),
                     'blocked': not st.user.is_active, 'until': st.blocked_until})
    rows.sort(key=lambda r: -r['count'])
    return render(request, 'suspicious_students.html', {'rows': rows})


def release_expired_blocks():
    """Снимает блокировки, срок которых вышел (аккаунт снова становится активным)."""
    from django.contrib.auth.models import User
    from django.utils import timezone
    expired = Student.objects.filter(user__is_active=False, blocked_until__isnull=False,
                                     blocked_until__lte=timezone.now())
    user_ids = list(expired.values_list('user_id', flat=True))
    if user_ids:
        User.objects.filter(id__in=user_ids).update(is_active=True)
        expired.update(blocked_until=None)


@staff_member_required
@require_POST
def student_toggle_block(request, student_id):
    """Блокировка: duration=temp — на BLOCK_HOURS часов, forever — бессрочно; если ученик
    уже заблокирован — разблокировка."""
    from datetime import timedelta
    from django.utils import timezone
    student = _managed_student_or_none(request, student_id)
    if student is None:
        messages.error(request, 'Это ученик не вашего класса.')
        return redirect('student_list')
    release_expired_blocks()
    user = student.user
    user.refresh_from_db()
    if user.is_active:
        user.is_active = False
        if request.POST.get('duration') == 'forever':
            student.blocked_until = None
            note = 'заблокирован без срока — войти он не сможет, пока вы его не разблокируете.'
        else:
            student.blocked_until = timezone.now() + timedelta(hours=BLOCK_HOURS)
            note = f'заблокирован на {BLOCK_HOURS} часов.'
    else:
        user.is_active = True
        student.blocked_until = None
        note = 'разблокирован.'
    user.save(update_fields=['is_active'])
    student.save(update_fields=['blocked_until'])
    messages.success(request, f'Ученик «{student.full_name}» {note}')
    nxt = request.POST.get('next')
    return redirect('suspicious_students' if nxt == 'suspicious' else 'student_list')


@staff_member_required
@require_POST
def student_toggle_duel_tester(request, student_id):
    """Включает/выключает ученику роль «тестер дуэлей» (видит дуэли в режиме «только тестеры»)."""
    from .duels import is_duels_admin
    if not is_duels_admin(request.user):
        messages.error(request, 'Назначать тестеров дуэлей может только администратор дуэлей.')
        return redirect('student_list')
    student = _managed_student_or_none(request, student_id)
    if student is None:
        messages.error(request, 'Это ученик не вашего класса.')
        return redirect('student_list')
    student.duel_tester = not student.duel_tester
    student.save(update_fields=['duel_tester'])
    messages.success(request, f'«{student.full_name}»: тестер дуэлей — {"да" if student.duel_tester else "нет"}.')
    return redirect(request.POST.get('next') or 'student_list')
