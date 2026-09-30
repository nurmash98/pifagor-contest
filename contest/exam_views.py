# -*- coding: utf-8 -*-
"""СОР / СОЧ (БЖБ / ТЖБ).

Учитель создаёт экзамен: тип, название, класс, время начала, длительность, задачи.
Ученик своего класса в это время нажимает «Начать», решает задачи (каждая — до 10 баллов
по автотестам, сложность не важна), может «Завершить» досрочно. Пока экзамен у ученика
идёт, Курсы / Все задачи / Kanban для него закрыты (см. contest/middleware.py).
Оценка за экзамен — средний балл по всем задачам экзамена (не сданная задача = 0).
"""
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .autotest import run_autotests
from .models import Exam, ExamAnswer, ExamAttempt, Student, Tag, Task, Teacher, normalize_school_class

EXAM_MAX_DURATION = 600          # минут
EXAM_SUBMIT_THROTTLE_SECONDS = 10  # повторная отправка одной задачи — не чаще (защита CPU)


def active_attempt_for(user, now=None):
    """Идущая сейчас попытка экзамена ученика (или None)."""
    now = now or timezone.now()
    qs = ExamAttempt.objects.filter(
        student__user=user, finished_at__isnull=True, exam__start_at__lte=now,
    ).select_related('exam')
    for attempt in qs:
        if attempt.is_active(now):
            return attempt
    return None


def _score(passed, total):
    return int(10 * passed / total + 0.5) if total else 0


# ---------------------------------------------------------------- общий список

@login_required
def exam_list(request):
    if request.user.is_staff:
        return _teacher_exam_list(request)
    student = get_object_or_404(Student, user=request.user)
    now = timezone.now()
    exams = list(Exam.objects.filter(school_class=student.school_class).prefetch_related('tasks'))
    attempts = {a.exam_id: a for a in ExamAttempt.objects.filter(student=student, exam__in=exams)
                .prefetch_related('answers')}
    active, upcoming, finished = [], [], []
    for exam in exams:
        exam.my_attempt = attempts.get(exam.id)
        exam.state = exam.status(now)
        if exam.my_attempt and not exam.my_attempt.is_active(now):
            exam.state = 'finished'
        if exam.state == 'finished':
            exam.my_grade = exam.my_attempt.grade() if exam.my_attempt else 0
            finished.append(exam)
        elif exam.state == 'active':
            active.append(exam)
        else:
            upcoming.append(exam)
    upcoming.sort(key=lambda e: e.start_at)
    return render(request, 'exams_student.html', {
        'active': active, 'upcoming': upcoming, 'finished': finished,
    })


# ---------------------------------------------------------------- ученик

def _student_exam_or_redirect(request, exam_id):
    student = Student.objects.filter(user=request.user).first()
    exam = get_object_or_404(Exam, id=exam_id)
    if student is None or exam.school_class != student.school_class:
        messages.error(request, 'Этот СОР/СОЧ не для вашего класса.')
        return None, None, None
    attempt = ExamAttempt.objects.filter(exam=exam, student=student).first()
    return student, exam, attempt


@login_required
def exam_detail(request, exam_id):
    if request.user.is_staff:
        return redirect('exam_results', exam_id=exam_id)
    student, exam, attempt = _student_exam_or_redirect(request, exam_id)
    if exam is None:
        return redirect('exam_list')
    now = timezone.now()
    state = exam.status(now)
    in_progress = attempt is not None and attempt.is_active(now)
    finished = state == 'finished' or (attempt is not None and not in_progress)

    tasks = list(exam.tasks.all())
    from .views import _apply_task_language, _get_content_lang
    _apply_task_language(tasks, _get_content_lang(request))
    answers = {a.task_id: a for a in attempt.answers.all()} if attempt else {}
    for task in tasks:
        task.answer = answers.get(task.id)

    return render(request, 'exam_detail.html', {
        'exam': exam, 'attempt': attempt, 'state': state, 'tasks': tasks,
        'in_progress': in_progress, 'finished': finished,
        'grade': attempt.grade() if (attempt and finished) else (0 if finished else None),
    })


@login_required
def exam_start(request, exam_id):
    if request.method != 'POST' or request.user.is_staff:
        return redirect('exam_list')
    student, exam, attempt = _student_exam_or_redirect(request, exam_id)
    if exam is None:
        return redirect('exam_list')
    if exam.status() != 'active':
        messages.warning(request, 'Сейчас этот СОР/СОЧ не идёт.')
    elif attempt is None:
        ExamAttempt.objects.get_or_create(exam=exam, student=student, defaults={'started_at': timezone.now()})
    return redirect('exam_detail', exam_id=exam.id)


@login_required
def exam_finish(request, exam_id):
    if request.method != 'POST' or request.user.is_staff:
        return redirect('exam_list')
    student, exam, attempt = _student_exam_or_redirect(request, exam_id)
    if attempt is not None and attempt.is_active():
        attempt.finished_at = timezone.now()
        attempt.save(update_fields=['finished_at'])
        messages.success(request, 'Вы завершили СОР/СОЧ.')
    return redirect('exam_detail', exam_id=exam_id)


@login_required
def exam_task(request, exam_id, task_id):
    if request.user.is_staff:
        return redirect('exam_results', exam_id=exam_id)
    student, exam, attempt = _student_exam_or_redirect(request, exam_id)
    if exam is None:
        return redirect('exam_list')
    task = get_object_or_404(exam.tasks, id=task_id)
    if attempt is None:
        return redirect('exam_detail', exam_id=exam.id)
    answer = ExamAnswer.objects.filter(attempt=attempt, task=task).first()
    active = attempt.is_active()

    if request.method == 'POST':
        if not active:
            messages.warning(request, 'Время СОР/СОЧ вышло — ответы больше не принимаются.')
            return redirect('exam_detail', exam_id=exam.id)
        throttle_key = f'exam_submit:{attempt.id}:{task.id}'
        if not cache.add(throttle_key, 1, EXAM_SUBMIT_THROTTLE_SECONDS):
            messages.warning(request, f'Подождите {EXAM_SUBMIT_THROTTLE_SECONDS} секунд перед повторной отправкой.')
            return redirect('exam_task', exam_id=exam.id, task_id=task.id)
        code = (request.POST.get('code') or '').replace('\r\n', '\n')
        result = run_autotests(task, code)
        passed, total = (result['passed'], result['total']) if result.get('available') else (0, len(task.test_cases or []))
        score = _score(passed, total)
        answer = answer or ExamAnswer(attempt=attempt, task=task)
        answer.code = code
        answer.submissions_count += 1
        answer.submitted_at = timezone.now()
        answer.tests_passed, answer.tests_total = passed, total
        answer.results = result.get('results', [])
        if score >= answer.best_score:
            answer.best_score, answer.best_code = score, code
        answer.save()
        messages.success(request, f'Пройдено тестов {passed} из {total} — {score} баллов. '
                                  f'Лучший результат по задаче: {answer.best_score}/10.')
        return redirect('exam_task', exam_id=exam.id, task_id=task.id)

    from .views import _apply_task_language, _get_content_lang
    _apply_task_language([task], _get_content_lang(request))
    return render(request, 'exam_task.html', {
        'exam': exam, 'task': task, 'answer': answer, 'active': active, 'attempt': attempt,
    })


# ---------------------------------------------------------------- учитель

def _teacher(user):
    return None if user.is_superuser else Teacher.objects.filter(user=user).first()


def _teacher_classes(user):
    teacher = _teacher(user)
    return None if teacher is None else teacher.class_list()


def _can_manage(user, exam):
    classes = _teacher_classes(user)
    if classes is None:
        return True
    teacher = _teacher(user)
    return exam.school_class in classes or (teacher is not None and exam.created_by_id == teacher.id)


def _teacher_exam_list(request):
    classes = _teacher_classes(request.user)
    exams = Exam.objects.prefetch_related('tasks').all()
    if classes is not None:
        teacher = _teacher(request.user)
        exams = [e for e in exams if e.school_class in classes or e.created_by_id == teacher.id]
    now = timezone.now()
    for exam in exams:
        exam.state = exam.status(now)
    return render(request, 'exams_teacher.html', {'exams': exams})


def _exam_form(request, exam):
    is_new = exam.pk is None
    classes = _teacher_classes(request.user)
    tasks = Task.objects.exclude(test_cases=[]).prefetch_related('tags').order_by('level', 'title')

    if request.method == 'POST':
        errors = []
        kind = request.POST.get('kind', '')
        title = request.POST.get('title', '').strip()
        school_class = normalize_school_class(request.POST.get('school_class', ''))
        start_raw = request.POST.get('start_at', '')
        duration_raw = request.POST.get('duration_minutes', '')
        task_ids = [int(t) for t in request.POST.getlist('tasks') if t.isdigit()]

        if kind not in ('SOR', 'SOCH'):
            errors.append('Выберите тип: СОР или СОЧ.')
        if not title:
            errors.append('Укажите название.')
        if not school_class:
            errors.append('Выберите класс.')
        elif classes is not None and school_class not in classes:
            errors.append('Можно создавать СОР/СОЧ только для своих классов.')
        start_at = None
        try:
            start_at = timezone.make_aware(datetime.strptime(start_raw, '%Y-%m-%dT%H:%M'))
        except (ValueError, TypeError):
            errors.append('Укажите дату и время начала.')
        try:
            duration = int(duration_raw)
            if not 1 <= duration <= EXAM_MAX_DURATION:
                raise ValueError
        except (ValueError, TypeError):
            duration = None
            errors.append(f'Длительность — от 1 до {EXAM_MAX_DURATION} минут.')
        chosen = list(tasks.filter(id__in=task_ids))
        if not chosen:
            errors.append('Добавьте хотя бы одну задачу.')

        if not errors:
            exam.kind, exam.title, exam.school_class = kind, title, school_class
            exam.start_at, exam.duration_minutes = start_at, duration
            if is_new:
                exam.created_by = _teacher(request.user)
            exam.save()
            exam.tasks.set(chosen)
            messages.success(request, f'{exam.get_kind_display()} «{exam.title}» сохранён: {len(chosen)} задач, '
                                      f'{timezone.localtime(exam.start_at):%d.%m.%Y %H:%M}, {duration} мин.')
            return redirect('exam_list')
        for e in errors:
            messages.error(request, e)
        values = {'kind': kind, 'title': title, 'school_class': school_class, 'start_at': start_raw,
                  'duration_minutes': duration_raw}
        selected = set(task_ids)
    else:
        values = {
            'kind': exam.kind or 'SOR', 'title': exam.title, 'school_class': exam.school_class,
            'start_at': timezone.localtime(exam.start_at).strftime('%Y-%m-%dT%H:%M') if exam.start_at else '',
            'duration_minutes': exam.duration_minutes or 40,
        }
        selected = set(exam.tasks.values_list('id', flat=True)) if not is_new else set()

    return render(request, 'exam_form.html', {
        'exam': exam, 'is_new': is_new, 'v': values, 'tasks': tasks, 'selected': selected,
        'class_choices': sorted(set(classes)) if classes is not None else None,
        'all_tags': Tag.objects.all(), 'max_duration': EXAM_MAX_DURATION,
    })


@staff_member_required
def exam_create(request):
    if _teacher_classes(request.user) == []:
        messages.error(request, 'Вам пока не назначены классы — обратитесь к администратору.')
        return redirect('exam_list')
    return _exam_form(request, Exam())


@staff_member_required
def exam_edit(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if not _can_manage(request.user, exam):
        messages.error(request, 'Это СОР/СОЧ не вашего класса.')
        return redirect('exam_list')
    if exam.status() != 'upcoming':
        messages.warning(request, 'СОР/СОЧ уже начался — изменить его нельзя.')
        return redirect('exam_results', exam_id=exam.id)
    return _exam_form(request, exam)


@staff_member_required
def exam_delete(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if request.method == 'POST' and _can_manage(request.user, exam):
        name = str(exam)
        exam.delete()
        messages.success(request, f'{name} удалён.')
    return redirect('exam_list')


@staff_member_required
def exam_results(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if not _can_manage(request.user, exam):
        messages.error(request, 'Это СОР/СОЧ не вашего класса.')
        return redirect('exam_list')
    now = timezone.now()
    tasks = list(exam.tasks.all())
    students = Student.objects.filter(school_class=exam.school_class).select_related('user').order_by('full_name')
    attempts = {a.student_id: a for a in exam.attempts.prefetch_related('answers')}
    rows = []
    for st in students:
        attempt = attempts.get(st.id)
        answers = {a.task_id: a for a in attempt.answers.all()} if attempt else {}
        if attempt is None:
            status = 'not_started'
        elif attempt.is_active(now):
            status = 'in_progress'
        else:
            status = 'finished'
        rows.append({
            'student': st, 'attempt': attempt, 'status': status,
            'cells': [answers.get(t.id) for t in tasks],
            'grade': attempt.grade() if attempt else 0,
        })
    return render(request, 'exam_results.html', {
        'exam': exam, 'tasks': tasks, 'rows': rows, 'state': exam.status(now),
    })
