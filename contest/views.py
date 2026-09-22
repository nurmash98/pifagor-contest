import logging
import re
from datetime import timedelta
from urllib.parse import quote

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, Case, When, Value, IntegerField, F
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone

from .models import Task, Submission, Student, Course, Teacher, Topic, Tag, ClassBonus
from .forms import StudentRegistrationForm
from .ai_review import run_ai_review, is_configured as ai_review_is_configured
from django.contrib.admin.views.decorators import staff_member_required

SUBMISSION_COOLDOWN = timedelta(hours=24)
logger = logging.getLogger(__name__)

# Баллы, которыми учитель может поощрить/наказать целый класс за атмосферу на уроке —
# без какой-либо строгой методики, просто когда учителю хочется. Обычные значения ±1/±2;
# +7 и -6 — крайние оценки для по-настоящему запомнившегося (в любую сторону) урока,
# ставятся редко (это объясняется в критериях на странице лидерборда, не в самой кнопке).
CLASS_BONUS_PRESETS = [
    (7, '+7 · Прям влюбился в класс на весь урок'),
    (2, '+2 · Имба-урок, класс был на волне'),
    (1, '+1 · Было комфортно работать'),
    (-1, '-1 · Не очень комфортно было'),
    (-2, '-2 · Вайб не тот, полный кринж'),
    (-6, '-6 · Вообще не понравилось, как сидели'),
]
CLASS_BONUS_ALLOWED_VALUES = {points for points, _ in CLASS_BONUS_PRESETS}


def _grade_of(school_class):
    """Параллель ученика — числовая часть класса без буквы: '7А' -> '7', '10Б' -> '10'."""
    match = re.match(r'\d+', (school_class or '').strip())
    return match.group() if match else (school_class or '').strip()


# Общий балл в лидерборде = сумма (коэффициент сложности задачи * оценка учителя за неё).
# Коэффициент зависит от уровня задачи: лёгкая — 1, средняя — 2, сложная — 3.
LEADERBOARD_COEFFICIENT_BY_LEVEL = {'A': 1, 'B': 2, 'C': 3}

LEADERBOARD_COEFFICIENT_CASE = Case(
    When(submissions__task__level='A', then=Value(LEADERBOARD_COEFFICIENT_BY_LEVEL['A'])),
    When(submissions__task__level='B', then=Value(LEADERBOARD_COEFFICIENT_BY_LEVEL['B'])),
    When(submissions__task__level='C', then=Value(LEADERBOARD_COEFFICIENT_BY_LEVEL['C'])),
    default=Value(0),
    output_field=IntegerField(),
)

# score * коэффициент — очки за одну решённую задачу.
LEADERBOARD_SCORE_EXPR = F('submissions__score') * LEADERBOARD_COEFFICIENT_CASE


def _class_leaderboard_rows(school_classes):
    """Рейтинг классов: средний балл (округлённый до целого) по ВСЕМ ученикам класса
    + бонусные баллы, которые классу вручную поставил учитель за атмосферу на уроке.
    school_classes — классы, которые нужно включить в таблицу (одной параллели)."""
    rows = []
    for cls in school_classes:
        students_in_class = Student.objects.filter(school_class=cls).annotate(
            total_score=Sum(LEADERBOARD_SCORE_EXPR, filter=Q(submissions__status='DONE')),
        )
        scores = [student.total_score or 0 for student in students_in_class]
        avg_score = round(sum(scores) / len(scores)) if scores else 0
        bonus = ClassBonus.objects.filter(school_class=cls).aggregate(total=Sum('points'))['total'] or 0
        rows.append({
            'school_class': cls,
            'student_count': len(scores),
            'avg_score': avg_score,
            'bonus': bonus,
            'total': avg_score + bonus,
        })
    rows.sort(key=lambda row: row['total'], reverse=True)
    return rows


def _cooldown_remaining(submission):
    """Сколько ещё осталось ждать до повторной отправки этой задачи (или None, если можно отправлять)."""
    if not submission.last_submitted_at:
        return None
    elapsed = timezone.now() - submission.last_submitted_at
    if elapsed >= SUBMISSION_COOLDOWN:
        return None
    return SUBMISSION_COOLDOWN - elapsed


def _safe_back_url(request):
    """Достаём ?back=... из запроса и проверяем, что это безопасная локальная ссылка,
    чтобы кнопка 'Назад' вела туда, откуда пользователь пришёл (каталог, курс, Kanban)."""
    raw_back = request.GET.get('back') or request.POST.get('back')
    if raw_back and url_has_allowed_host_and_scheme(
        raw_back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return raw_back
    return None


def _ai_review_is_stale(submission):
    """Нужно ли (пере)запускать ИИ-проверку: ещё не пробовали ни разу, либо ученик
    отправил код заново после последней попытки (last_submitted_at обновился)."""
    if submission.ai_checked_at is None:
        return True
    if submission.last_submitted_at and submission.last_submitted_at > submission.ai_checked_at:
        return True
    return False


def _refresh_ai_review(submission):
    """Запускает ИИ-проверку решения и сохраняет результат прямо в отправке — только
    подсказка (score/feedback/plagiarism_note), официальный балл преподаватель всё
    равно ставит сам. ai_checked_at ставится ВСЕГДА (успех или нет), чтобы не повторять
    попытку на каждой загрузке страницы, если она уже не удалась; ai_reviewed_at — только
    при успехе. Никогда не бросает исключение наружу (вызывается уже ПОСЛЕ
    submission.save() с новым кодом — не должно мешать ученику отправить решение)."""
    try:
        result = run_ai_review(submission)
    except Exception:
        logger.exception('Непредвиденная ошибка ИИ-проверки решения #%s', submission.pk)
        result = {'available': False}

    submission.ai_checked_at = timezone.now()
    update_fields = ['ai_checked_at']
    if result.get('available'):
        submission.ai_score = result.get('score')
        submission.ai_feedback = result.get('feedback', '')
        submission.ai_plagiarism_note = result.get('plagiarism_note', '')
        submission.ai_reviewed_at = timezone.now()
        update_fields += ['ai_score', 'ai_feedback', 'ai_plagiarism_note', 'ai_reviewed_at']
    submission.save(update_fields=update_fields)


def _get_content_lang(request):
    """Текущий выбранный язык условий задач ('ru' или 'kk'), из сессии пользователя."""
    lang = request.session.get('content_lang', 'ru')
    return lang if lang in ('ru', 'kk') else 'ru'


def _apply_task_language(tasks, lang):
    """Проставляет task.display_title/description/input_example/output_example
    с учётом выбранного языка — если казахского перевода нет, используем русский."""
    for task in tasks:
        task.display_title = task.get_display_title(lang)
        task.display_description = task.get_display_description(lang)
        task.display_input_example = task.get_display_input_example(lang)
        task.display_output_example = task.get_display_output_example(lang)
    return tasks


def set_content_lang(request, lang):
    """Переключатель языка условий задач (RU/KZ) в шапке сайта — сохраняем в сессии
    и возвращаем пользователя туда, откуда он переключил язык."""
    if lang in ('ru', 'kk'):
        request.session['content_lang'] = lang
    back_url = _safe_back_url(request)
    return redirect(back_url or 'all_tasks')


@login_required
def all_tasks_view(request):
    """Главная страница: каталог всех задач в виде таблицы.
    Ученику показываем его статус по каждой задаче; преподавателю — тот же каталог
    в режиме просмотра (без статусов решения, которых у него просто нет)."""
    student = Student.objects.filter(user=request.user).first()
    lang = _get_content_lang(request)

    level_filter = request.GET.get('level', '')
    tasks = Task.objects.prefetch_related('tags').all()
    if level_filter in ['A', 'B', 'C']:
        tasks = tasks.filter(level=level_filter)

    # Фильтр по тегу — доступен и ученикам, и преподавателям (общий каталог задач).
    tag_id_raw = request.GET.get('tag', '')
    selected_tag_id = None
    if tag_id_raw:
        try:
            selected_tag_id = int(tag_id_raw)
        except (TypeError, ValueError):
            selected_tag_id = None
    if selected_tag_id:
        tasks = tasks.filter(tags__id=selected_tag_id)

    tasks = list(tasks)
    _apply_task_language(tasks, lang)

    if student is not None:
        # Привязываем существующие решения ученика к задачам
        user_submissions = {
            sub.task_id: sub
            for sub in Submission.objects.filter(student=student)
        }
        for task in tasks:
            task.user_sub = user_submissions.get(task.id)
    else:
        for task in tasks:
            task.user_sub = None

    context = {
        'tasks': tasks,
        'current_level': level_filter,
        'current_tag': selected_tag_id,
        'all_tags': Tag.objects.all(),
        'readonly': student is None,
    }
    return render(request, 'all_tasks.html', context)


@login_required
def task_detail(request, task_id):
    """Детальная страница задачи и ручная отправка на проверку преподавателю.
    Преподаватель тоже может открыть любую задачу — но только для просмотра,
    без формы отправки решения (у него нет своих посылок)."""
    task = get_object_or_404(Task, id=task_id)
    student = Student.objects.filter(user=request.user).first()
    back_url = _safe_back_url(request)
    lang = _get_content_lang(request)
    _apply_task_language([task], lang)

    if student is None:
        # Преподаватель/админ — только просмотр условия задачи
        context = {
            'task': task,
            'submission': None,
            'cooldown_hours': None,
            'back_url': back_url or reverse('all_tasks'),
            'readonly': True,
        }
        return render(request, 'task_detail.html', context)

    task_url = reverse('task_detail', args=[task.id])
    task_url_with_back = f'{task_url}?back={quote(back_url, safe="")}' if back_url else task_url

    submission = Submission.objects.filter(student=student, task=task).first()

    # Если задача еще не взята в работу
    if not submission:
        active_count = Submission.objects.filter(student=student, status='IN_PROGRESS').count()
        if active_count >= 2:
            messages.warning(request, 'Вы не можете взять более 2 задач одновременно. Завершите текущие задачи!')
            return redirect(back_url or 'all_tasks')
        submission = Submission.objects.create(student=student, task=task, status='IN_PROGRESS')

    cooldown = _cooldown_remaining(submission)

    # ОБРАБОТКА ОТПРАВКИ КОДА (Ручная проверка) — не чаще одного раза в 24 часа
    if request.method == 'POST':
        if cooldown:
            hours_left = int(cooldown.total_seconds() // 3600) + 1
            messages.warning(request, f'Эту задачу можно отправлять раз в 24 часа. Попробуйте снова через {hours_left} ч.')
            return redirect(task_url_with_back)

        code = request.POST.get('code', '')
        submission.code = code
        submission.status = 'TESTING'  # Отправляем на проверку преподавателю
        submission.last_submitted_at = timezone.now()
        submission.save()
        _refresh_ai_review(submission)

        messages.success(request, 'Код отправлен! Преподаватель проверит ваше решение и выставит балл.')
        return redirect(task_url_with_back)

    context = {
        'task': task,
        'submission': submission,
        'cooldown_hours': int(cooldown.total_seconds() // 3600) + 1 if cooldown else None,
        'back_url': back_url or reverse('all_tasks'),
        'readonly': False,
    }
    return render(request, 'task_detail.html', context)


@login_required
def kanban_board(request):
    """Главная страница ученика: Kanban доска."""
    if request.user.is_staff:
        return redirect('teacher_dashboard')
    student = get_object_or_404(Student, user=request.user)
    lang = _get_content_lang(request)

    all_tasks = Task.objects.all()
    submissions = Submission.objects.select_related('task').filter(student=student)

    # Оставляем это QuerySet-ами (не списками), т.к. шаблон вызывает .count на них —
    # но принудительно вычисляем их один раз, чтобы проставить язык задачам и закешировать результат.
    in_progress = submissions.filter(status='IN_PROGRESS')
    testing = submissions.filter(status='TESTING')
    done = submissions.filter(status='DONE')
    _apply_task_language(
        [sub.task for sub in list(in_progress) + list(testing) + list(done)], lang
    )

    active_task_ids = submissions.values_list('task_id', flat=True)
    backlog_tasks = all_tasks.exclude(id__in=active_task_ids)
    _apply_task_language(list(backlog_tasks), lang)

    context = {
        'student': student,
        'backlog_tasks': backlog_tasks,
        'in_progress': in_progress,
        'testing': testing,
        'done': done,
    }
    return render(request, 'kanban.html', context)


@login_required
def take_task(request, task_id):
    """Перенос задачи в статус 'В работе' прямо с Kanban-доски или каталога"""
    if request.method == 'POST':
        student = get_object_or_404(Student, user=request.user)
        task = get_object_or_404(Task, id=task_id)

        active_count = Submission.objects.filter(student=student, status='IN_PROGRESS').count()
        if active_count >= 2:
            messages.warning(request,
                             'Вы не можете взять более 2 задач одновременно. Завершите текущие задачи на Kanban-доске!')
            return redirect('all_tasks')

        Submission.objects.get_or_create(
            student=student,
            task=task,
            defaults={'status': 'IN_PROGRESS'}
        )
    return redirect('kanban')


@login_required
def submit_code(request, submission_id):
    """Сохранение кода (для быстрой формы в Kanban, если она используется)"""
    if request.method == 'POST':
        submission = get_object_or_404(Submission, id=submission_id, student__user=request.user)

        cooldown = _cooldown_remaining(submission)
        if cooldown:
            hours_left = int(cooldown.total_seconds() // 3600) + 1
            messages.warning(request, f'Эту задачу можно отправлять раз в 24 часа. Попробуйте снова через {hours_left} ч.')
            return redirect('kanban')

        code = request.POST.get('code', '')
        submission.code = code
        submission.status = 'TESTING'
        submission.last_submitted_at = timezone.now()
        submission.save()
        _refresh_ai_review(submission)

        messages.success(request, 'Код отправлен на проверку преподавателю!')

    return redirect('kanban')


@login_required
def profile_view(request):
    """Профиль ученика: общий балл (как в лидерборде — коэффициент сложности * оценка за
    КАЖДУЮ проверенную задачу) и количество РЕШЁННЫХ задач — тех, где оценка 10/10
    (а не любых проверенных). Средний балл здесь не показывается."""
    student = get_object_or_404(Student, user=request.user)

    # Вся история проверенных задач (для списка "История решений" — с любой оценкой).
    solved_submissions = Submission.objects.filter(
        student=student, status='DONE'
    ).select_related('task').order_by('-updated_at')

    # "Решено" — только задачи, где оценка максимальная (10/10).
    solved_count = solved_submissions.filter(score=10).count()
    total_score = sum(
        sub.score * LEADERBOARD_COEFFICIENT_BY_LEVEL.get(sub.task.level, 0)
        for sub in solved_submissions
    )

    context = {
        'student': student,
        'solved_submissions': solved_submissions,
        'solved_count': solved_count,
        'total_score': total_score,
    }
    return render(request, 'profile.html', context)


@login_required
def leaderboard(request):
    """Лидерборд. Ученик видит топ-10 своей параллели (например, все 7-е классы вместе,
    независимо от буквы). Учитель видит ВСЕХ учеников своих классов (без ограничения топ-10),
    с фильтром по конкретному классу (по списку классов в его профиле).
    Админ (суперпользователь) без своего класса видит топ-10 по всей школе."""
    student = Student.objects.filter(user=request.user).first()
    my_grade = None
    scope_label = None
    teacher_classes = None
    selected_class = ''
    limit = 10

    if student is not None:
        my_grade = _grade_of(student.school_class)
        scope_label = f'{my_grade} классы'
    elif not request.user.is_superuser:
        teacher = Teacher.objects.filter(user=request.user).first()
        if teacher is not None:
            teacher_classes = teacher.class_list()
            requested_class = request.GET.get('class', '').strip()
            selected_class = requested_class if requested_class in teacher_classes else ''
            scope_label = selected_class or ', '.join(teacher_classes)
            limit = None  # Учитель видит полный список своих классов, без топ-10

    # Общий балл = сумма (коэффициент сложности задачи * оценка учителя) по КАЖДОЙ проверенной
    # задаче. «Решено» — это только задачи с оценкой 10/10 (perfect_count), а не любые
    # проверенные (graded_count используем лишь чтобы не показывать тех, кто ничего не
    # отправлял или ещё ничего не проверено).
    students_query = Student.objects.annotate(
        total_score=Sum(LEADERBOARD_SCORE_EXPR, filter=Q(submissions__status='DONE')),
        graded_count=Count('submissions', filter=Q(submissions__status='DONE')),
        perfect_count=Count('submissions', filter=Q(submissions__status='DONE', submissions__score=10)),
    ).filter(graded_count__gt=0).order_by('-total_score', '-perfect_count', '-graded_count')

    if my_grade is not None:
        # Фильтруем в Python, т.к. класс — свободный текст ("7А", "10Б"),
        # а нужна именно числовая параллель без буквы.
        students_query = [s for s in students_query if _grade_of(s.school_class) == my_grade]
    elif teacher_classes is not None:
        if selected_class:
            # Учитель выбрал конкретный класс из фильтра
            students_query = [s for s in students_query if s.school_class == selected_class]
        else:
            # Без фильтра — все ученики всех классов учителя (точное совпадение, например "10А", "11Б")
            students_query = [s for s in students_query if s.school_class in teacher_classes]

    students_list = list(students_query)
    if limit is not None:
        students_list = students_list[:limit]

    students = []
    for student_row in students_list:
        student_row.calc_total_score = student_row.total_score or 0
        # "Решено задач" в таблице — это perfect_count (оценка 10/10), не graded_count.
        student_row.calc_solved_count = student_row.perfect_count or 0
        students.append(student_row)

    # ---- Рейтинг классов этой параллели (или параллелей, если у учителя классы из
    # разных параллелей / у админа нет выбранного фильтра) ----
    all_school_classes = list(
        Student.objects.order_by('school_class').values_list('school_class', flat=True).distinct()
    )

    if my_grade is not None:
        relevant_grades = [my_grade]
    elif teacher_classes is not None:
        if selected_class:
            relevant_grades = [_grade_of(selected_class)]
        else:
            relevant_grades = sorted({_grade_of(c) for c in teacher_classes})
    else:
        # Суперпользователь без выбранного класса — показываем рейтинг по каждой параллели школы.
        relevant_grades = sorted({_grade_of(c) for c in all_school_classes})

    class_leaderboards = []
    for grade in relevant_grades:
        classes_in_grade = sorted({c for c in all_school_classes if _grade_of(c) == grade})
        if classes_in_grade:
            class_leaderboards.append({'grade': grade, 'rows': _class_leaderboard_rows(classes_in_grade)})

    # Классы, которым текущий пользователь (учитель/админ) может ставить бонусные баллы.
    can_add_class_bonus = request.user.is_staff
    bonus_classes = teacher_classes if teacher_classes is not None else (
        all_school_classes if request.user.is_superuser else []
    )

    return render(request, 'leaderboard.html', {
        'students': students,
        'my_grade': my_grade,
        'scope_label': scope_label,
        'teacher_classes': teacher_classes,
        'selected_class': selected_class,
        'is_full_list': limit is None,
        'class_leaderboards': class_leaderboards,
        'can_add_class_bonus': can_add_class_bonus,
        'bonus_classes': bonus_classes,
        'class_bonus_presets': CLASS_BONUS_PRESETS,
    })


@staff_member_required
def add_class_bonus(request):
    """Учитель (или админ) ставит целому классу бонусные/штрафные баллы за атмосферу
    на уроке — вручную, кнопкой на странице лидерборда. Никакой методики: баллы
    ставятся просто когда учителю хочется. Учитель может ставить баллы только
    своим классам; админ (суперпользователь) — любому классу школы."""
    if request.method != 'POST':
        return redirect('leaderboard')

    school_class = request.POST.get('school_class', '').strip()
    comment = request.POST.get('comment', '').strip()
    try:
        points = int(request.POST.get('points', ''))
    except (TypeError, ValueError):
        points = None

    teacher = _teacher_or_none(request)
    allowed_classes = teacher.class_list() if teacher is not None else None

    back_url = _safe_back_url(request)

    if points not in CLASS_BONUS_ALLOWED_VALUES:
        messages.error(request, 'Некорректное количество баллов.')
    elif not school_class or (allowed_classes is not None and school_class not in allowed_classes):
        messages.error(request, 'Вы можете ставить баллы только своим классам.')
    else:
        ClassBonus.objects.create(
            school_class=school_class, points=points, given_by=teacher, comment=comment,
        )
        sign = '+' if points > 0 else ''
        messages.success(request, f'Классу {school_class} начислено {sign}{points} балл(ов).')

    return redirect(back_url or 'leaderboard')


def register(request):
    if request.user.is_authenticated:
        return redirect('all_tasks')

    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            full_name = form.cleaned_data['full_name']
            school_class = form.cleaned_data['school_class']

            if User.objects.filter(username=username).exists():
                form.add_error('username', 'Этот логин уже занят. Придумайте другой.')
            else:
                user = User.objects.create_user(username=username, password=password)
                Student.objects.create(
                    user=user,
                    full_name=full_name,
                    school_class=school_class
                )
                auth_login(request, user)
                return redirect('all_tasks')
    else:
        form = StudentRegistrationForm()

    return render(request, 'register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('teacher_dashboard' if request.user.is_staff else 'all_tasks')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            auth_login(request, user)
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('teacher_dashboard' if user.is_staff else 'all_tasks')
        else:
            return render(request, 'login.html', {'error': 'Неверный логин или пароль'})

    return render(request, 'login.html')


def logout_view(request):
    auth_logout(request)
    return redirect('login')


@login_required
def courses_view(request):
    """Каталог курсов, видимых ученикам (и открытый учителям/админу для проверки)."""
    courses = Course.objects.prefetch_related('topics__tasks')
    if not request.user.is_staff:
        courses = courses.filter(is_visible=True)
    return render(request, 'courses.html', {'courses': courses})


@login_required
def course_detail(request, course_id):
    """Темы и задачи внутри одного курса — со статусом решения для ученика."""
    course = get_object_or_404(Course, id=course_id)
    if not course.is_visible and not request.user.is_staff:
        messages.warning(request, 'Этот курс пока недоступен.')
        return redirect('courses')

    topics = course.topics.prefetch_related('tasks__tags').all()
    lang = _get_content_lang(request)
    for topic in topics:
        _apply_task_language(topic.tasks.all(), lang)

    if not request.user.is_staff:
        student = Student.objects.filter(user=request.user).first()
        if student:
            user_submissions = {
                sub.task_id: sub for sub in Submission.objects.filter(student=student)
            }
            for topic in topics:
                for task in topic.tasks.all():
                    task.user_sub = user_submissions.get(task.id)

    return render(request, 'course_detail.html', {'course': course, 'topics': topics})


def _teacher_or_none(request):
    return None if request.user.is_superuser else Teacher.objects.filter(user=request.user).first()


def _get_owned_course_or_none(request, course_id):
    """Курс, если текущий пользователь — админ или один из назначенных ему учителей."""
    course = get_object_or_404(Course, id=course_id)
    teacher = _teacher_or_none(request)
    if teacher is not None and not course.teachers.filter(pk=teacher.pk).exists():
        return None
    return course


@staff_member_required
def course_create(request):
    """Учитель (или админ) создаёт новый курс прямо в приложении, без Django admin."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Укажите название курса.')
        else:
            teacher = _teacher_or_none(request)
            course = Course.objects.create(
                name=name,
                description=request.POST.get('description', '').strip(),
                is_visible=request.POST.get('is_visible') == 'on',
                created_by=teacher,
            )
            if teacher is not None:
                teacher.courses.add(course)
            messages.success(request, f'Курс «{course.name}» создан. Теперь добавьте темы.')
            return redirect('course_manage', course_id=course.id)

    return render(request, 'course_form.html', {})


@staff_member_required
def course_manage(request, course_id):
    """Страница курса для учителя: темы, теория, задачи — без захода в админку."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    topics = course.topics.prefetch_related('tasks').all()
    return render(request, 'course_manage.html', {'course': course, 'topics': topics})


@staff_member_required
def course_analytics(request, course_id):
    """Список классов -> при выборе класса таблица: ученики x темы, средний балл (не решил — 0 баллов)."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    teacher = _teacher_or_none(request)
    if teacher is not None:
        classes = teacher.class_list()
    else:
        classes = list(
            Student.objects.order_by('school_class')
            .values_list('school_class', flat=True).distinct()
        )

    selected_class = request.GET.get('class', '').strip()
    if selected_class not in classes:
        selected_class = classes[0] if classes else ''

    topics = list(course.topics.prefetch_related('tasks').order_by('order', 'name'))
    topic_task_ids = [(topic, list(topic.tasks.values_list('id', flat=True))) for topic in topics]

    students = []
    if selected_class:
        class_students = Student.objects.filter(school_class=selected_class).order_by('full_name')

        # Все баллы DONE-решений учеников этого класса: {(student_id, task_id): score}
        scores = {
            (s['student_id'], s['task_id']): s['score']
            for s in Submission.objects.filter(
                status='DONE', student__school_class=selected_class
            ).values('student_id', 'task_id', 'score')
        }

        for student in class_students:
            cells = []
            overall_total = 0
            overall_possible = 0
            for topic, task_ids in topic_task_ids:
                total = sum(scores.get((student.id, tid), 0) for tid in task_ids)
                average = round(total / len(task_ids), 1) if task_ids else 0
                cells.append({'topic': topic, 'average': average})
                overall_total += total
                overall_possible += len(task_ids)
            overall_average = round(overall_total / overall_possible, 1) if overall_possible else 0
            students.append({
                'student': student,
                'cells': cells,
                'overall_average': overall_average,
            })

    context = {
        'course': course,
        'classes': classes,
        'selected_class': selected_class,
        'topics': topics,
        'students': students,
    }
    return render(request, 'course_analytics.html', context)


@staff_member_required
def course_edit(request, course_id):
    """Изменение названия/описания/видимости курса."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            course.name = name
        course.description = request.POST.get('description', '').strip()
        course.is_visible = request.POST.get('is_visible') == 'on'
        course.save()
        messages.success(request, 'Курс обновлён.')

    return redirect('course_manage', course_id=course.id)


@staff_member_required
def topic_create(request, course_id):
    """Добавление темы курса: название, необязательная теория (ссылка на YouTube), задачи."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    all_tasks = Task.objects.prefetch_related('tags').order_by('level', 'title')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Укажите название темы.')
        else:
            topic = Topic.objects.create(
                course=course,
                name=name,
                order=request.POST.get('order') or 0,
                theory_video_url=request.POST.get('theory_video_url', '').strip(),
            )
            topic.tasks.set(request.POST.getlist('tasks'))
            messages.success(request, f'Тема «{topic.name}» добавлена.')
            return redirect('course_manage', course_id=course.id)

    return render(request, 'topic_form.html', {
        'course': course, 'topic': None, 'all_tasks': all_tasks, 'selected_task_ids': set(),
        'all_tags': Tag.objects.all(),
    })


@staff_member_required
def topic_edit(request, topic_id):
    """Редактирование темы: название, теория, порядок, набор задач."""
    topic = get_object_or_404(Topic, id=topic_id)
    course = _get_owned_course_or_none(request, topic.course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    all_tasks = Task.objects.prefetch_related('tags').order_by('level', 'title')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            topic.name = name
        topic.order = request.POST.get('order') or topic.order
        topic.theory_video_url = request.POST.get('theory_video_url', '').strip()
        topic.save()
        topic.tasks.set(request.POST.getlist('tasks'))
        messages.success(request, f'Тема «{topic.name}» обновлена.')
        return redirect('course_manage', course_id=course.id)

    return render(request, 'topic_form.html', {
        'course': course, 'topic': topic, 'all_tasks': all_tasks,
        'selected_task_ids': set(topic.tasks.values_list('id', flat=True)),
        'all_tags': Tag.objects.all(),
    })


@staff_member_required
def task_tags_list(request):
    """Полный список всех задач для управления тегами — доступен любому учителю,
    вне привязки к его классам или курсам."""
    tasks = Task.objects.prefetch_related('tags').order_by('level', 'title')

    level_filter = request.GET.get('level', '')
    if level_filter in ['A', 'B', 'C']:
        tasks = tasks.filter(level=level_filter)

    q = request.GET.get('q', '').strip()
    if q:
        tasks = tasks.filter(title__icontains=q)

    return render(request, 'task_tags_list.html', {
        'tasks': tasks, 'level_filter': level_filter, 'q': q,
    })


@staff_member_required
def task_tags_edit(request, task_id):
    """Изменение тегов одной задачи — доступно любому учителю для любой задачи."""
    task = get_object_or_404(Task, id=task_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_new_tag':
            new_tag_name = request.POST.get('new_tag', '').strip()
            if new_tag_name:
                tag, _ = Tag.objects.get_or_create(name=new_tag_name)
                task.tags.add(tag)
                messages.success(request, f'Тег «{tag.name}» добавлен к задаче.')
        else:
            task.tags.set(request.POST.getlist('tags'))
            messages.success(request, 'Теги задачи обновлены.')
        return redirect('task_tags_edit', task_id=task.id)

    return render(request, 'task_tags_edit.html', {
        'task': task,
        'all_tags': Tag.objects.all(),
        'selected_tag_ids': set(task.tags.values_list('id', flat=True)),
    })


@staff_member_required
def student_list(request):
    """Полный список всех учеников — доступен любому учителю, вне привязки к его классам."""
    students = Student.objects.select_related('user').order_by('school_class', 'full_name')

    class_filter = request.GET.get('class', '').strip()
    if class_filter:
        students = students.filter(school_class=class_filter)

    q = request.GET.get('q', '').strip()
    if q:
        students = students.filter(Q(full_name__icontains=q) | Q(user__username__icontains=q))

    all_classes = (
        Student.objects.order_by('school_class').values_list('school_class', flat=True).distinct()
    )

    return render(request, 'student_list.html', {
        'students': students, 'all_classes': all_classes, 'class_filter': class_filter, 'q': q,
    })


@staff_member_required
def student_create(request):
    """Добавление нового ученика (логин, пароль, имя, класс) — доступно любому учителю."""
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        full_name = request.POST.get('full_name', '').strip()
        school_class = request.POST.get('school_class', '').strip()

        if not username or not password or not full_name or not school_class:
            messages.error(request, 'Заполните логин, пароль, имя и класс.')
        elif User.objects.filter(username=username).exists():
            messages.error(request, 'Этот логин уже занят.')
        else:
            user = User.objects.create_user(username=username, password=password)
            Student.objects.create(user=user, full_name=full_name, school_class=school_class)
            messages.success(request, f'Ученик «{full_name}» добавлен.')
            return redirect('student_list')

    return render(request, 'student_form.html', {'mode': 'create'})


@staff_member_required
def student_edit(request, student_id):
    """Изменение логина, пароля, имени и класса ученика — доступно любому учителю."""
    student = get_object_or_404(Student, id=student_id)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        full_name = request.POST.get('full_name', '').strip()
        school_class = request.POST.get('school_class', '').strip()

        if not username or not full_name or not school_class:
            messages.error(request, 'Заполните логин, имя и класс.')
        elif User.objects.filter(username=username).exclude(id=student.user_id).exists():
            messages.error(request, 'Этот логин уже занят другим пользователем.')
        else:
            student.user.username = username
            if password:
                student.user.set_password(password)
            student.user.save()
            student.full_name = full_name
            student.school_class = school_class
            student.save()
            messages.success(request, 'Данные ученика обновлены.')
            return redirect('student_list')

    return render(request, 'student_form.html', {'mode': 'edit', 'student': student})


@staff_member_required
def student_delete(request, student_id):
    """Удаление ученика вместе с его аккаунтом и решениями — доступно любому учителю."""
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        name = student.full_name
        student.user.delete()  # каскадом удаляет Student и его Submission
        messages.success(request, f'Ученик «{name}» удалён.')
    return redirect('student_list')


@login_required
def topic_video(request, topic_id):
    """Отдельная страница с видеоуроком темы — открывается по клику с страницы курса,
    чтобы само видео не занимало много места в списке задач."""
    topic = get_object_or_404(Topic, id=topic_id)
    course = topic.course
    if not course.is_visible and not request.user.is_staff:
        messages.warning(request, 'Этот курс пока недоступен.')
        return redirect('courses')
    if not topic.theory_video_url:
        messages.info(request, 'Для этой темы видеоурок пока не добавлен.')
        return redirect('course_detail', course_id=course.id)

    return render(request, 'topic_video.html', {'topic': topic, 'course': course})


@staff_member_required
def teacher_dashboard(request):
    """Кабинет преподавателя: очередь на проверку и последние проверенные."""
    teacher = None if request.user.is_superuser else Teacher.objects.filter(user=request.user).first()

    pending_submissions = Submission.objects.filter(status='TESTING')
    graded_submissions = Submission.objects.filter(status='DONE')
    my_courses = Course.objects.all()

    if teacher is not None:
        # Учитель видит только очередь и статистику по своим классам и курсам.
        class_list = teacher.class_list()
        pending_submissions = pending_submissions.filter(student__school_class__in=class_list)
        graded_submissions = graded_submissions.filter(student__school_class__in=class_list)
        my_courses = teacher.courses.all()

    pending_submissions = pending_submissions.order_by('updated_at')
    graded_submissions = graded_submissions.order_by('-updated_at')[:20]

    context = {
        'pending_submissions': pending_submissions,
        'graded_submissions': graded_submissions,
        'teacher': teacher,
        'my_courses': my_courses,
    }
    return render(request, 'teacher_dashboard.html', context)


@staff_member_required
def grade_submission(request, submission_id):
    """Страница проверки конкретного решения."""
    submission = get_object_or_404(Submission, id=submission_id)

    # Учитель проверяет только заявки своих классов — чужие ему недоступны.
    if not request.user.is_superuser:
        teacher = Teacher.objects.filter(user=request.user).first()
        if teacher is not None and submission.student.school_class not in teacher.class_list():
            messages.error(request, 'Это не ваш класс — проверка недоступна.')
            return redirect('teacher_dashboard')

    if request.method == 'POST':
        score = request.POST.get('score', 0)
        comment = request.POST.get('comment', '')

        submission.score = int(score)
        submission.teacher_comment = comment
        submission.status = 'DONE'
        submission.save()

        messages.success(request, f'Оценка {score}/10 сохранена. Ученик получит ваш комментарий!')
        return redirect('teacher_dashboard')

    # Подстраховка: если ИИ-проверка ещё не бегала для этой отправки (например, решение
    # существует с тех пор, как эта функция появилась, либо предыдущая попытка не
    # прошла) — пробуем прямо сейчас, перед тем как показать страницу учителю.
    if ai_review_is_configured() and _ai_review_is_stale(submission):
        _refresh_ai_review(submission)

    return render(request, 'grade_submission.html', {
        'submission': submission,
        'ai_configured': ai_review_is_configured(),
    })