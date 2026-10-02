# -*- coding: utf-8 -*-
"""СОР / СОЧ (БЖБ / ТЖБ).

Учитель создаёт экзамен: тип, название, класс, время начала, длительность, задачи.
Ученик своего класса в это время нажимает «Начать», решает задачи (каждая — до 10 баллов
по автотестам, сложность не важна), может «Завершить» досрочно. Пока экзамен у ученика
идёт, Курсы / Все задачи / Kanban для него закрыты (см. contest/middleware.py).
Оценка за экзамен — средний балл по всем задачам экзамена (не сданная задача = 0).

Задачи — либо «определённые» (все ученики решают все выбранные задачи), либо «случайные»:
учитель задаёт количество задач и для каждой — тег и сложность, а при нажатии «Начать»
компьютер каждому ученику по каждому условию выбирает случайную задачу (без повторов).
Оценка — средний балл по выпавшим задачам.
"""
import random
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
EXAM_MAX_RANDOM_SLOTS = 20
EXAM_MAX_SUBMISSIONS = 3           # сколько раз можно отправить решение одной задачи на СОР/СОЧ


def _slot_candidates(slot):
    """id задач с тестами, подходящих под условие {'tag': id|None, 'level': 'A'|'B'|'C'|''}."""
    qs = Task.objects.exclude(test_cases=[])
    if slot.get('tag'):
        qs = qs.filter(tags__id=slot['tag'])
    if slot.get('level'):
        qs = qs.filter(level=slot['level'])
    return list(qs.values_list('id', flat=True).distinct())


def _random_match(candidates):
    """Каждому условию — своя случайная задача, без повторов (паросочетание Куна по
    перемешанным спискам). Возвращает список id (None — если задачи для условия не хватило)."""
    cands = [random.sample(c, len(c)) for c in candidates]
    owner = {}

    def place(i, seen):
        for t in cands[i]:
            if t in seen:
                continue
            seen.add(t)
            if t not in owner or place(owner[t], seen):
                owner[t] = i
                return True
        return False

    for i in range(len(cands)):
        place(i, set())
    result = [None] * len(cands)
    for t, i in owner.items():
        result[i] = t
    return result


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

    if attempt is not None:
        tasks = attempt.task_list()
    else:
        # Набор для случайного выбора ученику не показываем — только его выпавшие задачи.
        tasks = [] if exam.is_random else list(exam.tasks.all())
    from .views import _apply_task_language, _get_content_lang
    _apply_task_language(tasks, _get_content_lang(request))
    answers = {a.task_id: a for a in attempt.answers.all()} if attempt else {}
    for task in tasks:
        task.answer = answers.get(task.id)

    return render(request, 'exam_detail.html', {
        'exam': exam, 'attempt': attempt, 'state': state, 'tasks': tasks,
        'in_progress': in_progress, 'finished': finished, 'task_count': exam.tasks_per_student(),
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
        task_ids = []
        if exam.random_slots:
            picked = _random_match([_slot_candidates(sl) for sl in exam.random_slots])
            task_ids = [t for t in picked if t is not None]
        elif exam.is_random:   # старый вариант: N случайных из отмеченного набора
            pool = list(exam.tasks.values_list('id', flat=True))
            task_ids = random.sample(pool, min(exam.random_count, len(pool)))
        ExamAttempt.objects.get_or_create(exam=exam, student=student,
                                          defaults={'started_at': timezone.now(), 'task_ids': task_ids})
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
    task = get_object_or_404(Task, id=task_id)
    if attempt is None:
        return redirect('exam_detail', exam_id=exam.id)
    if task.id not in {t.id for t in attempt.task_list()}:
        messages.warning(request, 'Эта задача вам не выпала.')
        return redirect('exam_detail', exam_id=exam.id)
    answer = ExamAnswer.objects.filter(attempt=attempt, task=task).first()
    active = attempt.is_active()

    if request.method == 'POST':
        if not active:
            messages.warning(request, 'Время СОР/СОЧ вышло — ответы больше не принимаются.')
            return redirect('exam_detail', exam_id=exam.id)
        if answer is not None and answer.submissions_count >= EXAM_MAX_SUBMISSIONS:
            messages.warning(request, f'По этой задаче уже использованы все {EXAM_MAX_SUBMISSIONS} отправки.')
            return redirect('exam_task', exam_id=exam.id, task_id=task.id)
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
        left = EXAM_MAX_SUBMISSIONS - answer.submissions_count
        messages.success(request, f'Пройдено тестов {passed} из {total} — {score} баллов. '
                                  f'Лучший результат по задаче: {answer.best_score}/10. '
                                  f'Осталось отправок: {left} из {EXAM_MAX_SUBMISSIONS}.')
        return redirect('exam_task', exam_id=exam.id, task_id=task.id)

    from .views import _apply_task_language, _get_content_lang
    _apply_task_language([task], _get_content_lang(request))
    return render(request, 'exam_task.html', {
        'exam': exam, 'task': task, 'answer': answer, 'active': active, 'attempt': attempt,
        'max_submissions': EXAM_MAX_SUBMISSIONS,
        'submissions_left': EXAM_MAX_SUBMISSIONS - (answer.submissions_count if answer else 0),
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
        mode = 'random' if request.POST.get('mode') == 'random' else 'fixed'
        slot_tags = request.POST.getlist('slot_tag')
        slot_levels = request.POST.getlist('slot_level')

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
        chosen, slots = [], []
        if mode == 'fixed':
            chosen = list(tasks.filter(id__in=task_ids))
            if not chosen:
                errors.append('Добавьте хотя бы одну задачу.')
        else:
            tag_ids = set(Tag.objects.values_list('id', flat=True))
            for tag_raw, level in zip(slot_tags, slot_levels):
                tag = int(tag_raw) if tag_raw.isdigit() and int(tag_raw) in tag_ids else None
                slots.append({'tag': tag, 'level': level if level in ('A', 'B', 'C') else ''})
            if not 1 <= len(slots) <= EXAM_MAX_RANDOM_SLOTS:
                errors.append(f'Количество случайных задач — от 1 до {EXAM_MAX_RANDOM_SLOTS}.')
            else:
                candidates = [_slot_candidates(sl) for sl in slots]
                empty = [i for i, c in enumerate(candidates, 1) if not c]
                if empty:
                    errors.append('Нет задач с тестами под условие задачи №' + ', №'.join(map(str, empty))
                                  + ' — выберите другой тег или сложность.')
                elif None in _random_match(candidates):
                    errors.append('Под одинаковые условия не хватает разных задач — одному ученику '
                                  'задачи не повторяются. Измените тег или сложность.')
        random_count = len(slots)

        if not errors:
            exam.kind, exam.title, exam.school_class = kind, title, school_class
            exam.start_at, exam.duration_minutes = start_at, duration
            exam.random_count = random_count
            exam.random_slots = slots
            if is_new:
                exam.created_by = _teacher(request.user)
            exam.save()
            exam.tasks.set(chosen)
            what = (f'каждому {random_count} случайных задач по тегу и сложности' if random_count
                    else f'{len(chosen)} задач')
            messages.success(request, f'{exam.get_kind_display()} «{exam.title}» сохранён: {what}, '
                                      f'{timezone.localtime(exam.start_at):%d.%m.%Y %H:%M}, {duration} мин.')
            return redirect('exam_list')
        for e in errors:
            messages.error(request, e)
        values = {'kind': kind, 'title': title, 'school_class': school_class, 'start_at': start_raw,
                  'duration_minutes': duration_raw, 'mode': mode}
        slot_values = [{'tag': sl['tag'], 'level': sl['level']} for sl in slots] if mode == 'random' else None
        selected = set(task_ids)
    else:
        values = {
            'kind': exam.kind or 'SOR', 'title': exam.title, 'school_class': exam.school_class,
            'start_at': timezone.localtime(exam.start_at).strftime('%Y-%m-%dT%H:%M') if exam.start_at else '',
            'duration_minutes': exam.duration_minutes or 40,
            'mode': 'random' if exam.random_count else 'fixed',
        }
        slot_values = [{'tag': sl.get('tag'), 'level': sl.get('level') or ''} for sl in exam.random_slots] or None
        selected = set(exam.tasks.values_list('id', flat=True)) if not is_new else set()

    return render(request, 'exam_form.html', {
        'exam': exam, 'is_new': is_new, 'v': values, 'tasks': tasks, 'selected': selected,
        'class_choices': sorted(set(classes)) if classes is not None else None,
        'all_tags': Tag.objects.all(), 'max_duration': EXAM_MAX_DURATION,
        'levels': Task.LEVEL_CHOICES, 'max_slots': EXAM_MAX_RANDOM_SLOTS,
        'slots': slot_values or [{'tag': None, 'level': ''} for _ in range(3)],
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
        if exam.is_random:
            # Колонки «№1..№N»: в каждой — выпавшая задача и балл за неё.
            mine = attempt.task_list() if attempt else []
            cells = [{'task': t, 'answer': answers.get(t.id)} for t in mine]
            cells += [{'task': None, 'answer': None}] * (exam.random_count - len(cells))
        else:
            cells = [{'task': t, 'answer': answers.get(t.id)} for t in tasks]
        rows.append({
            'student': st, 'attempt': attempt, 'status': status,
            'cells': cells,
            'grade': attempt.grade() if attempt else 0,
        })
    labels = [sl['label'] for sl in exam.slot_list()]
    columns = ([f'№{i}' + (f' {labels[i - 1]}' if i <= len(labels) else '') for i in range(1, exam.random_count + 1)]
               if exam.is_random
               else [f'{i}. {t.title}' for i, t in enumerate(tasks, 1)])
    return render(request, 'exam_results.html', {
        'exam': exam, 'tasks': tasks, 'rows': rows, 'state': exam.status(now), 'columns': columns,
        'slot_labels': labels,
    })
