# -*- coding: utf-8 -*-
"""СОР / СОЧ (БЖБ / ТЖБ).

Учитель создаёт экзамен: тип, название, класс, время начала, длительность, задачи.
Ученик своего класса в это время нажимает «Начать», решает задачи (каждая — до 10 баллов
по автотестам, сложность не важна), может «Завершить» досрочно. Пока экзамен у ученика
идёт, Курсы / Все задачи для него закрыты (см. contest/middleware.py).
Оценка за экзамен — средний балл по всем задачам экзамена (не сданная задача = 0).

Задачи — либо «определённые» (все ученики решают все выбранные задачи), либо «случайные»:
учитель задаёт количество задач и для каждой — тег и сложность, а при нажатии «Начать»
компьютер каждому ученику по каждому условию выбирает случайную задачу (без повторов).
Оценка — средний балл по выпавшим задачам.

Пересдача: после окончания экзамена учитель нажимает «Пересдача», выбирает конкретных
учеников и назначает время. Каждому из них при старте выпадают ДРУГИЕ случайные задачи
(не те, что были на экзамене). Оценка за экзамен остаётся как есть, а оценка за пересдачу
хранится отдельно — после пересдачи у ученика видны обе.
"""
import random
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .autotest import run_autotests
from .models import (Exam, ExamAnswer, ExamAttempt, ExamRetake, Student, Tag, Task, Teacher,
                     normalize_school_class)

EXAM_MAX_DURATION = 600          # минут
EXAM_SUBMIT_THROTTLE_SECONDS = 10  # повторная отправка одной задачи — не чаще (защита CPU)
EXAM_MAX_RANDOM_SLOTS = 20
EXAM_MAX_SUBMISSIONS = 3           # сколько раз можно отправить решение одной задачи на СОР/СОЧ


def _slot_candidates(slot, exclude=()):
    """id задач с тестами, подходящих под условие {'tag': id|None, 'level': 'A'|'B'|'C'|''}.
    exclude — id задач, которые выбирать нельзя."""
    qs = Task.objects.exclude(test_cases=[])
    if exclude:
        qs = qs.exclude(id__in=list(exclude))
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


def _retake_slots(exam, prev_attempt):
    """Условия задач для пересдачи: те же, что у экзамена (тег + сложность). Если экзамен
    был с «определёнными» задачами — условие берётся от каждой его задачи (её сложность и тег)."""
    if exam.random_slots:
        return [dict(sl) for sl in exam.random_slots]
    if prev_attempt is not None and prev_attempt.task_ids:
        base = prev_attempt.task_list()
    else:
        base = list(exam.tasks.all())[:max(exam.tasks_per_student(), 1)]
    slots = []
    for task in base:
        tag_id = task.tags.order_by('id').values_list('id', flat=True).first()
        slots.append({'tag': tag_id, 'level': task.level})
    return slots


def _retake_task_ids(exam, student):
    """Случайные задачи ученику на пересдачу — НЕ те, что были у него на экзамене.
    Если под условие (тег + сложность) не хватает других задач, условие ослабляется:
    сначала без тега, потом без сложности. В самом крайнем случае (в базе почти нет задач
    с тестами) допускаются повторы с экзамена, но внутри пересдачи задачи не повторяются."""
    prev = ExamAttempt.objects.filter(exam=exam, student=student, retake__isnull=True).first()
    if prev is not None:
        seen = {t.id for t in prev.task_list()}
    elif not exam.is_random:
        seen = set(exam.tasks.values_list('id', flat=True))
    else:
        seen = set()
    slots = _retake_slots(exam, prev)

    def relaxed(slot, level):
        return {'tag': slot.get('tag') if level < 1 else None,
                'level': slot.get('level') if level < 2 else ''}

    result = [None] * len(slots)
    taken = set(seen)
    for level in (0, 1, 2):
        todo = [i for i, t in enumerate(result) if t is None]
        if not todo:
            break
        picked = _random_match([_slot_candidates(relaxed(slots[i], level), taken) for i in todo])
        for i, task_id in zip(todo, picked):
            if task_id is not None:
                result[i] = task_id
                taken.add(task_id)
    todo = [i for i, t in enumerate(result) if t is None]
    if todo:   # крайний случай: разрешаем повторы с экзамена
        used = {t for t in result if t is not None}
        picked = _random_match([_slot_candidates(relaxed(slots[i], 2), used) for i in todo])
        for i, task_id in zip(todo, picked):
            result[i] = task_id
    return [t for t in result if t is not None]


def active_attempt_for(user, now=None):
    """Идущая сейчас попытка экзамена или пересдачи ученика (или None)."""
    now = now or timezone.now()
    qs = ExamAttempt.objects.filter(
        student__user=user, finished_at__isnull=True,
    ).filter(
        Q(retake__isnull=True, exam__start_at__lte=now) | Q(retake__start_at__lte=now),
    ).select_related('exam', 'retake')
    for attempt in qs:
        if attempt.is_active(now):
            return attempt
    return None


def _score(passed, total):
    return int(10 * passed / total + 0.5) if total else 0


# ---------------------------------------------------------------- общий список

def _to_detail(exam, retake=None):
    if retake is not None:
        return redirect('retake_detail', exam_id=exam.id, retake_id=retake.id)
    return redirect('exam_detail', exam_id=exam.id)


def _attempt_state(window, attempt, now):
    """'upcoming' / 'active' / 'finished' для экзамена или пересдачи с учётом досрочного завершения."""
    state = window.status(now)
    if attempt is not None and not attempt.is_active(now):
        state = 'finished'
    return state


@login_required
def exam_list(request):
    if request.user.is_staff:
        return _teacher_exam_list(request)
    student = get_object_or_404(Student, user=request.user)
    now = timezone.now()
    exams = list(Exam.objects.filter(school_class=student.school_class).prefetch_related('tasks'))
    attempts = {a.exam_id: a for a in ExamAttempt.objects.filter(student=student, exam__in=exams, retake__isnull=True)
                .prefetch_related('answers')}
    retakes = list(ExamRetake.objects.filter(students=student, exam__in=exams).select_related('exam'))
    retake_attempts = {a.retake_id: a for a in ExamAttempt.objects.filter(student=student, retake__in=retakes)
                       .prefetch_related('answers')}
    retake_by_exam = {}
    active_retakes, upcoming_retakes = [], []
    for retake in retakes:               # у ученика на экзамен — одна пересдача (берём последнюю)
        retake.my_attempt = retake_attempts.get(retake.id)
        retake.state = _attempt_state(retake, retake.my_attempt, now)
        if retake.state == 'finished':
            retake.my_grade = retake.my_attempt.grade() if retake.my_attempt else 0
        if retake.exam_id not in retake_by_exam or retake.start_at > retake_by_exam[retake.exam_id].start_at:
            retake_by_exam[retake.exam_id] = retake
        if retake.state == 'active':
            active_retakes.append(retake)
        elif retake.state == 'upcoming':
            upcoming_retakes.append(retake)
    upcoming_retakes.sort(key=lambda r: r.start_at)

    active, upcoming, finished = [], [], []
    for exam in exams:
        exam.my_attempt = attempts.get(exam.id)
        exam.state = _attempt_state(exam, exam.my_attempt, now)
        exam.my_retake = retake_by_exam.get(exam.id)
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
        'active_retakes': active_retakes, 'upcoming_retakes': upcoming_retakes,
    })


# ---------------------------------------------------------------- ученик

def _student_exam_or_redirect(request, exam_id, retake_id=None):
    """(ученик, экзамен, пересдача, попытка). Для пересдачи ученик должен быть в её списке."""
    student = Student.objects.filter(user=request.user).first()
    exam = get_object_or_404(Exam, id=exam_id)
    if student is None or exam.school_class != student.school_class:
        messages.error(request, 'Этот СОР/СОЧ не для вашего класса.')
        return None, None, None, None
    retake = None
    if retake_id is not None:
        retake = get_object_or_404(ExamRetake, id=retake_id, exam=exam)
        if not retake.students.filter(id=student.id).exists():
            messages.error(request, 'Эта пересдача назначена не вам.')
            return None, None, None, None
    attempt = ExamAttempt.objects.filter(exam=exam, student=student, retake=retake).first()
    return student, exam, retake, attempt


def _grade_info(window, attempt, now):
    """(завершено, оценка) для экзамена или пересдачи; оценка None, пока не завершено."""
    done = window.status(now) == 'finished' or (attempt is not None and not attempt.is_active(now))
    if not done:
        return False, None
    return True, (attempt.grade() if attempt else 0)


@login_required
def exam_detail(request, exam_id, retake_id=None):
    if request.user.is_staff:
        return redirect('exam_results', exam_id=exam_id)
    student, exam, retake, attempt = _student_exam_or_redirect(request, exam_id, retake_id)
    if exam is None:
        return redirect('exam_list')
    now = timezone.now()
    window = retake or exam
    state = window.status(now)
    in_progress = attempt is not None and attempt.is_active(now)
    finished = state == 'finished' or (attempt is not None and not in_progress)

    if attempt is not None:
        tasks = attempt.task_list()
    else:
        # Набор для случайного выбора ученику не показываем — только его выпавшие задачи.
        tasks = [] if (exam.is_random or retake) else list(exam.tasks.all())
    from .views import _apply_task_language, _get_content_lang
    _apply_task_language(tasks, _get_content_lang(request))
    answers = {a.task_id: a for a in attempt.answers.all()} if attempt else {}
    for task in tasks:
        task.answer = answers.get(task.id)

    # Обе оценки: за экзамен и за пересдачу (пересдача — только у тех, кому её назначили).
    main_attempt = attempt if retake is None else \
        ExamAttempt.objects.filter(exam=exam, student=student, retake__isnull=True).first()
    my_retake = retake or ExamRetake.objects.filter(exam=exam, students=student).order_by('-start_at').first()
    retake_attempt = None
    if my_retake is not None:
        retake_attempt = attempt if retake else \
            ExamAttempt.objects.filter(exam=exam, student=student, retake=my_retake).first()
    exam_done, exam_grade = _grade_info(exam, main_attempt, now)
    retake_done, retake_grade = _grade_info(my_retake, retake_attempt, now) if my_retake else (False, None)
    my_retake_state = _attempt_state(my_retake, retake_attempt, now) if my_retake else None

    return render(request, 'exam_detail.html', {
        'exam': exam, 'retake': retake, 'window': window, 'attempt': attempt, 'state': state, 'tasks': tasks,
        'in_progress': in_progress, 'finished': finished, 'task_count': exam.tasks_per_student(),
        'grade': (attempt.grade() if attempt else 0) if finished else None,
        'my_retake': my_retake, 'my_retake_state': my_retake_state,
        'exam_done': exam_done, 'exam_grade': exam_grade,
        'retake_done': retake_done, 'retake_grade': retake_grade,
    })


@login_required
def exam_start(request, exam_id, retake_id=None):
    if request.method != 'POST' or request.user.is_staff:
        return redirect('exam_list')
    student, exam, retake, attempt = _student_exam_or_redirect(request, exam_id, retake_id)
    if exam is None:
        return redirect('exam_list')
    if (retake or exam).status() != 'active':
        messages.warning(request, 'Сейчас эта пересдача не идёт.' if retake else 'Сейчас этот СОР/СОЧ не идёт.')
    elif attempt is None:
        task_ids = []
        if retake is not None:
            task_ids = _retake_task_ids(exam, student)
        elif exam.random_slots:
            picked = _random_match([_slot_candidates(sl) for sl in exam.random_slots])
            task_ids = [t for t in picked if t is not None]
        elif exam.is_random:   # старый вариант: N случайных из отмеченного набора
            pool = list(exam.tasks.values_list('id', flat=True))
            task_ids = random.sample(pool, min(exam.random_count, len(pool)))
        ExamAttempt.objects.get_or_create(exam=exam, student=student, retake=retake,
                                          defaults={'started_at': timezone.now(), 'task_ids': task_ids})
    return _to_detail(exam, retake)


@login_required
def exam_finish(request, exam_id, retake_id=None):
    if request.method != 'POST' or request.user.is_staff:
        return redirect('exam_list')
    student, exam, retake, attempt = _student_exam_or_redirect(request, exam_id, retake_id)
    if exam is None:
        return redirect('exam_list')
    if attempt is not None and attempt.is_active():
        attempt.finished_at = timezone.now()
        attempt.save(update_fields=['finished_at'])
        messages.success(request, 'Вы завершили пересдачу.' if retake else 'Вы завершили СОР/СОЧ.')
    return _to_detail(exam, retake)


@login_required
def exam_task(request, exam_id, task_id, retake_id=None):
    if request.user.is_staff:
        return redirect('exam_results', exam_id=exam_id)
    student, exam, retake, attempt = _student_exam_or_redirect(request, exam_id, retake_id)
    if exam is None:
        return redirect('exam_list')
    task = get_object_or_404(Task, id=task_id)
    if attempt is None:
        return _to_detail(exam, retake)
    if task.id not in {t.id for t in attempt.task_list()}:
        messages.warning(request, 'Эта задача вам не выпала.')
        return _to_detail(exam, retake)
    answer = ExamAnswer.objects.filter(attempt=attempt, task=task).first()
    active = attempt.is_active()

    def back_to_task():
        if retake is not None:
            return redirect('retake_task', exam_id=exam.id, retake_id=retake.id, task_id=task.id)
        return redirect('exam_task', exam_id=exam.id, task_id=task.id)

    if request.method == 'POST':
        if not active:
            messages.warning(request, 'Время СОР/СОЧ вышло — ответы больше не принимаются.')
            return _to_detail(exam, retake)
        if answer is not None and answer.submissions_count >= EXAM_MAX_SUBMISSIONS:
            messages.warning(request, f'По этой задаче уже использованы все {EXAM_MAX_SUBMISSIONS} отправки.')
            return back_to_task()
        throttle_key = f'exam_submit:{attempt.id}:{task.id}'
        if not cache.add(throttle_key, 1, EXAM_SUBMIT_THROTTLE_SECONDS):
            messages.warning(request, f'Подождите {EXAM_SUBMIT_THROTTLE_SECONDS} секунд перед повторной отправкой.')
            return back_to_task()
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
        return back_to_task()

    from .views import _apply_task_language, _get_content_lang
    _apply_task_language([task], _get_content_lang(request))
    return render(request, 'exam_task.html', {
        'exam': exam, 'retake': retake, 'window': retake or exam, 'task': task, 'answer': answer,
        'active': active, 'attempt': attempt,
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
        exam.retake_count = exam.retakes.count()
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


def _result_rows(exam, students, attempts, now, per_student):
    """Строки таблицы результатов. per_student — у каждого ученика свои задачи (случайный
    экзамен или пересдача): колонки «№1..№N», в каждой выпавшая задача и балл."""
    tasks = list(exam.tasks.all())
    count = exam.tasks_per_student()
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
        if per_student:
            mine = attempt.task_list() if attempt else []
            cells = [{'task': t, 'answer': answers.get(t.id)} for t in mine]
            cells += [{'task': None, 'answer': None}] * (count - len(cells))
        else:
            cells = [{'task': t, 'answer': answers.get(t.id)} for t in tasks]
        rows.append({'student': st, 'attempt': attempt, 'status': status, 'cells': cells,
                     'grade': attempt.grade() if attempt else 0})
    return rows


def _result_columns(exam, per_student):
    labels = [sl['label'] for sl in exam.slot_list()]
    if per_student:
        return [f'№{i}' + (f' {labels[i - 1]}' if i <= len(labels) else '')
                for i in range(1, exam.tasks_per_student() + 1)], labels
    return [f'{i}. {t.title}' for i, t in enumerate(exam.tasks.all(), 1)], labels


@staff_member_required
def exam_results(request, exam_id, retake_id=None):
    exam = get_object_or_404(Exam, id=exam_id)
    if not _can_manage(request.user, exam):
        messages.error(request, 'Это СОР/СОЧ не вашего класса.')
        return redirect('exam_list')
    now = timezone.now()
    tasks = list(exam.tasks.all())
    class_students = Student.objects.filter(school_class=exam.school_class).select_related('user').order_by('full_name')
    retake = get_object_or_404(ExamRetake, id=retake_id, exam=exam) if retake_id is not None else None

    exam_attempts = {a.student_id: a for a in exam.attempts.filter(retake__isnull=True).prefetch_related('answers')}
    if retake is not None:
        students = retake.students.select_related('user').order_by('full_name')
        attempts = {a.student_id: a for a in retake.attempts.prefetch_related('answers')}
        rows = _result_rows(exam, students, attempts, now, per_student=True)
        for row in rows:    # рядом — оценка того же ученика за сам экзамен
            ea = exam_attempts.get(row['student'].id)
            row['exam_grade'] = ea.grade() if ea else 0
        state = retake.status(now)
        per_student = True
    else:
        rows = _result_rows(exam, class_students, exam_attempts, now, per_student=exam.is_random)
        state = exam.status(now)
        per_student = exam.is_random

    retakes = list(exam.retakes.prefetch_related('students').all())
    if retake is None and retakes:
        # Ученикам, назначенным на пересдачу, — вторая оценка (после окончания пересдачи).
        retake_attempts = {(a.retake_id, a.student_id): a
                           for a in ExamAttempt.objects.filter(exam=exam, retake__in=retakes)}
        assigned = {}
        for rt in retakes:
            for st in rt.students.all():
                assigned[st.id] = rt
        for row in rows:
            rt = assigned.get(row['student'].id)
            row['retake'] = rt
            if rt is None:
                continue
            ra = retake_attempts.get((rt.id, row['student'].id))
            rstate = _attempt_state(rt, ra, now)
            row['retake_state'] = rstate
            row['retake_grade'] = (ra.grade() if ra else 0) if rstate == 'finished' else None
    for rt in retakes:
        rt.state = rt.status(now)
        rt.student_names = ', '.join(s.full_name for s in rt.students.all())
        rt.attempt_count = rt.attempts.count()

    columns, labels = _result_columns(exam, per_student)
    return render(request, 'exam_results.html', {
        'exam': exam, 'tasks': tasks, 'rows': rows, 'state': state, 'columns': columns,
        'slot_labels': labels, 'retake': retake, 'retakes': retakes,
        'has_retakes': bool(retakes), 'per_student': per_student,
        'exam_finished': exam.status(now) == 'finished',
    })


@staff_member_required
def retake_create(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if not _can_manage(request.user, exam):
        messages.error(request, 'Это СОР/СОЧ не вашего класса.')
        return redirect('exam_list')
    now = timezone.now()
    if exam.status(now) != 'finished':
        messages.warning(request, 'Пересдачу можно назначить только после окончания СОР/СОЧ.')
        return redirect('exam_results', exam_id=exam.id)

    exam_attempts = {a.student_id: a for a in exam.attempts.filter(retake__isnull=True).prefetch_related('answers')}
    in_retake = {}
    for rt in exam.retakes.prefetch_related('students'):
        for st in rt.students.all():
            in_retake[st.id] = rt
    students = list(Student.objects.filter(school_class=exam.school_class).order_by('full_name'))
    for st in students:
        attempt = exam_attempts.get(st.id)
        st.exam_grade = attempt.grade() if attempt else 0
        st.took_part = attempt is not None
        st.existing_retake = in_retake.get(st.id)
    allowed = {st.id for st in students if st.existing_retake is None}

    if request.method == 'POST':
        errors = []
        chosen_ids = {int(x) for x in request.POST.getlist('students') if x.isdigit()} & allowed
        start_raw = request.POST.get('start_at', '')
        duration_raw = request.POST.get('duration_minutes', '')
        if not chosen_ids:
            errors.append('Выберите хотя бы одного ученика для пересдачи.')
        start_at = None
        try:
            start_at = timezone.make_aware(datetime.strptime(start_raw, '%Y-%m-%dT%H:%M'))
        except (ValueError, TypeError):
            errors.append('Укажите дату и время начала пересдачи.')
        try:
            duration = int(duration_raw)
            if not 1 <= duration <= EXAM_MAX_DURATION:
                raise ValueError
        except (ValueError, TypeError):
            duration = None
            errors.append(f'Длительность — от 1 до {EXAM_MAX_DURATION} минут.')
        if start_at and duration and start_at + timedelta(minutes=duration) <= now:
            errors.append('Время пересдачи уже прошло — укажите время в будущем.')
        if not errors:
            retake = ExamRetake.objects.create(exam=exam, start_at=start_at, duration_minutes=duration,
                                               created_by=_teacher(request.user))
            retake.students.set(Student.objects.filter(id__in=chosen_ids))
            messages.success(request, f'Пересдача назначена: {len(chosen_ids)} уч., '
                                      f'{timezone.localtime(start_at):%d.%m.%Y %H:%M}, {duration} мин. '
                                      f'Каждому выпадут другие случайные задачи.')
            return redirect('exam_results', exam_id=exam.id)
        for e in errors:
            messages.error(request, e)
        values = {'start_at': start_raw, 'duration_minutes': duration_raw}
        selected = chosen_ids
    else:
        soon = timezone.localtime(now + timedelta(minutes=30)).replace(second=0, microsecond=0)
        soon = soon.replace(minute=soon.minute - soon.minute % 5)
        values = {'start_at': soon.strftime('%Y-%m-%dT%H:%M'), 'duration_minutes': exam.duration_minutes}
        selected = set()
    return render(request, 'retake_form.html', {
        'exam': exam, 'students': students, 'selected': selected, 'v': values,
        'max_duration': EXAM_MAX_DURATION, 'task_count': exam.tasks_per_student(),
    })


@staff_member_required
def retake_delete(request, exam_id, retake_id):
    exam = get_object_or_404(Exam, id=exam_id)
    retake = get_object_or_404(ExamRetake, id=retake_id, exam=exam)
    if request.method == 'POST' and _can_manage(request.user, exam):
        if retake.attempts.exists():
            messages.warning(request, 'Ученики уже начали эту пересдачу — удалить её нельзя, чтобы не потерять оценки.')
        else:
            retake.delete()
            messages.success(request, 'Пересдача удалена.')
    return redirect('exam_results', exam_id=exam.id)
