"""Подозрительно быстрые решения: список для учителя и блокировка/разблокировка ученика.

Решение считается «слишком быстрым», если первая отправка по задаче пришла не позже
FAST_SOLVE_SECONDS после открытия задачи и прошла ВСЕ автотесты (10/10). Ученик попадает в
список, когда таких задач у него не меньше FAST_SOLVE_MIN_TASKS. Система только помечает —
блокирует (is_active=False) всегда учитель."""
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Attempt, Student
from .views import _managed_classes, _managed_student_or_none

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


@staff_member_required
def suspicious_students(request):
    classes = _managed_classes(request.user)
    students = Student.objects.select_related('user')
    if classes is not None:
        students = students.filter(school_class__in=classes)
    fast = fast_solves(students)
    rows = []
    for st in students.filter(id__in=[i for i, v in fast.items() if len(v) >= FAST_SOLVE_MIN_TASKS]) \
            .order_by('school_class', 'full_name'):
        items = fast[st.id]
        rows.append({'student': st, 'count': len(items), 'items': items[:8],
                     'blocked': not st.user.is_active})
    rows.sort(key=lambda r: -r['count'])
    return render(request, 'suspicious_students.html', {
        'rows': rows, 'seconds': FAST_SOLVE_SECONDS, 'min_tasks': FAST_SOLVE_MIN_TASKS})


@staff_member_required
@require_POST
def student_toggle_block(request, student_id):
    student = _managed_student_or_none(request, student_id)
    if student is None:
        messages.error(request, 'Это ученик не вашего класса.')
        return redirect('student_list')
    user = student.user
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    messages.success(request, f'Ученик «{student.full_name}» ' +
                     ('разблокирован.' if user.is_active else 'заблокирован — войти он больше не сможет.'))
    nxt = request.POST.get('next')
    return redirect('suspicious_students' if nxt == 'suspicious' else 'student_list')
