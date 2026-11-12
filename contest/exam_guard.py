"""СОР/СОЧ в полноэкранном режиме — как в игре «Бомба».

Работа идёт на странице-«оболочке» (exam_run): она одна разворачивается на весь экран, а задачи,
отправки и переходы открываются внутри неё в рамке (iframe), поэтому полный экран не слетает.
Любой выход — Esc, другое окно или программа (Alt+Tab), другая вкладка, обновление страницы —
записывается с временем; на MAX_EXITS-м выходе работа завершается. Попытка привязана к одному
браузеру: открыть её в другом нельзя. Учитель может вернуть доступ (если у ученика сломался компьютер).
"""
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import ExamAttempt, Student

MAX_EXITS = 3
DEDUPE_SECONDS = 3          # Esc + потеря фокуса одновременно — это один выход
KINDS = {
    'fullscreen': 'вышел из полноэкранного режима',
    'blur': 'переключился на другое окно или программу',
    'hidden': 'свернул окно или открыл другую вкладку',
    'reload': 'обновил или заново открыл страницу',
    'nav': 'ушёл со страницы СОР/СОЧ',
    'device': 'пытался открыть в другом браузере (не засчитано)',
    'restored': 'учитель вернул доступ',
}


def client_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def run_url(attempt):
    if attempt.retake_id:
        return reverse('retake_run', args=[attempt.exam_id, attempt.retake_id])
    return reverse('exam_run', args=[attempt.exam_id])


def detail_url(attempt):
    return attempt.get_absolute_url()


def _log(attempt, kind, **extra):
    entry = {'at': timezone.now().isoformat(), 'kind': kind}
    entry.update(extra)
    attempt.exit_log = list(attempt.exit_log or []) + [entry]


def record_exit(attempt, kind):
    """Засчитать выход (с защитой от двойного счёта). Возвращает обновлённую попытку."""
    with transaction.atomic():
        attempt = ExamAttempt.objects.select_for_update().get(pk=attempt.pk)
        if not attempt.is_active():
            return attempt
        now = timezone.now()
        counted = [e for e in (attempt.exit_log or []) if e.get('counted')]
        if counted:
            last = timezone.datetime.fromisoformat(counted[-1]['at'])
            if (now - last).total_seconds() < DEDUPE_SECONDS:
                return attempt
        attempt.exits += 1
        _log(attempt, kind if kind in KINDS else 'fullscreen', counted=True, n=attempt.exits)
        fields = ['exits', 'exit_log']
        if attempt.exits >= MAX_EXITS:
            attempt.finished_at = now
            attempt.finish_reason = 'exits'
            fields += ['finished_at', 'finish_reason']
        attempt.save(update_fields=fields)
        return attempt


def guard(request, attempt):
    """Для страниц СОР/СОЧ ученика (список задач, задача). None — можно показывать страницу."""
    if attempt is None or not attempt.is_active():
        return None
    me = client_key(request)
    if attempt.client and attempt.client != me:
        return render(request, 'exam_locked.html', {'attempt': attempt, 'exam': attempt.exam})
    # Открыли напрямую, а не внутри полноэкранной оболочки — ведём в оболочку.
    if request.method == 'GET' and request.headers.get('Sec-Fetch-Dest') == 'document':
        return redirect(run_url(attempt))
    return None


def _student_attempt(request, exam_id, retake_id=None):
    from .exam_views import _student_exam_or_redirect
    student, exam, retake, attempt = _student_exam_or_redirect(request, exam_id, retake_id)
    return exam, retake, attempt


@login_required
def exam_run(request, exam_id, retake_id=None):
    if request.user.is_staff:
        return redirect('exam_results', exam_id=exam_id)
    exam, retake, attempt = _student_attempt(request, exam_id, retake_id)
    if exam is None:
        return redirect('exam_list')
    if attempt is None or not attempt.is_active():
        return redirect(reverse('retake_detail', args=[exam.id, retake.id]) if retake
                        else reverse('exam_detail', args=[exam.id]))
    me = client_key(request)
    if attempt.client and attempt.client != me:
        _log(attempt, 'device')
        attempt.save(update_fields=['exit_log'])
        return render(request, 'exam_locked.html', {'attempt': attempt, 'exam': exam})
    if attempt.client == me:
        # Попытка уже шла в полном экране, а оболочку открыли заново (обновили, закрыли и открыли).
        attempt = record_exit(attempt, 'reload')
        if not attempt.is_active():
            return redirect(detail_url(attempt))
    return render(request, 'exam_run.html', {
        'exam': exam, 'retake': retake, 'attempt': attempt, 'window': retake or exam,
        'max_exits': MAX_EXITS, 'frame_url': detail_url(attempt),
    })


def _own_attempt(request, attempt_id):
    student = Student.objects.filter(user=request.user).first()
    return get_object_or_404(ExamAttempt, id=attempt_id, student=student)


def _state(attempt, ok=True):
    return JsonResponse({'ok': ok, 'exits': attempt.exits, 'max': MAX_EXITS,
                         'active': attempt.is_active(), 'detail': detail_url(attempt)})


@login_required
@require_POST
def exam_arm(request, attempt_id):
    """Оболочка развернулась на весь экран: попытка привязывается к этому браузеру."""
    attempt = _own_attempt(request, attempt_id)
    me = client_key(request)
    if attempt.is_active():
        ExamAttempt.objects.filter(pk=attempt.pk, client='').update(client=me)
        attempt.refresh_from_db()
    return _state(attempt, ok=attempt.is_active() and attempt.client == me)


@login_required
@require_POST
def exam_leave(request, attempt_id):
    """Браузер сообщает о выходе из полноэкранного режима (или потере фокуса)."""
    attempt = _own_attempt(request, attempt_id)
    if attempt.client and attempt.client != client_key(request):
        return _state(attempt, ok=False)
    attempt = record_exit(attempt, request.POST.get('kind', 'fullscreen'))
    return _state(attempt)


@staff_member_required
@require_POST
def exam_restore(request, attempt_id):
    """Учитель возвращает доступ: снова открыть работу, обнулить выходы, разрешить другой компьютер."""
    from .exam_views import _can_manage
    attempt = get_object_or_404(ExamAttempt.objects.select_related('exam', 'retake', 'student'), id=attempt_id)
    exam = attempt.exam
    back = (reverse('retake_results', args=[exam.id, attempt.retake_id]) if attempt.retake_id
            else reverse('exam_results', args=[exam.id]))
    if not _can_manage(request.user, exam):
        messages.error(request, 'Это СОР/СОЧ не вашего класса.')
        return redirect('exam_list')
    if timezone.now() >= attempt.window.end_at:
        messages.warning(request, f'Время уже вышло — вернуть доступ нельзя. Назначьте ученику пересдачу.')
        return redirect(back)
    teacher = getattr(request.user, 'teacher', None)
    _log(attempt, 'restored', by=(teacher.full_name if teacher else request.user.username),
         exits_before=attempt.exits)
    attempt.finished_at = None
    attempt.finish_reason = ''
    attempt.exits = 0
    attempt.client = ''
    attempt.save(update_fields=['exit_log', 'finished_at', 'finish_reason', 'exits', 'client'])
    messages.success(request, f'Доступ возвращён: {attempt.student.full_name} может продолжить '
                              f'(на этом или другом компьютере), счётчик выходов обнулён.')
    return redirect(back)


def exit_rows(attempt):
    """Журнал выходов для учителя: [(время, что произошло, засчитан ли)]."""
    rows = []
    for e in attempt.exit_log or []:
        try:
            at = timezone.localtime(timezone.datetime.fromisoformat(e['at']))
        except (KeyError, ValueError):
            continue
        text = KINDS.get(e.get('kind'), e.get('kind', ''))
        if e.get('kind') == 'restored' and e.get('by'):
            text += f' ({e["by"]})'
        rows.append({'at': at, 'text': text, 'counted': bool(e.get('counted')), 'n': e.get('n'),
                     'restored': e.get('kind') == 'restored'})
    return rows
