from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone

from .models import Task, Submission, Student, Course, Teacher, Topic, Tag
from .forms import StudentRegistrationForm
from django.contrib.admin.views.decorators import staff_member_required

SUBMISSION_COOLDOWN = timedelta(hours=24)


def _cooldown_remaining(submission):
    """Сколько ещё осталось ждать до повторной отправки этой задачи (или None, если можно отправлять)."""
    if not submission.last_submitted_at:
        return None
    elapsed = timezone.now() - submission.last_submitted_at
    if elapsed >= SUBMISSION_COOLDOWN:
        return None
    return SUBMISSION_COOLDOWN - elapsed


@login_required
def all_tasks_view(request):
    """Главная страница: каталог всех задач в виде таблицы."""

    # Если это преподаватель (админ) — отправляем его в кабинет учителя
    if request.user.is_staff:
        return redirect('teacher_dashboard')

    # Если это обычный ученик — показываем каталог задач
    student = get_object_or_404(Student, user=request.user)

    level_filter = request.GET.get('level', '')
    tasks = Task.objects.prefetch_related('tags').all()
    if level_filter in ['A', 'B', 'C']:
        tasks = tasks.filter(level=level_filter)

    # Привязываем существующие решения ученика к задачам
    user_submissions = {
        sub.task_id: sub
        for sub in Submission.objects.filter(student=student)
    }

    for task in tasks:
        task.user_sub = user_submissions.get(task.id)

    context = {
        'tasks': tasks,
        'current_level': level_filter,
    }
    return render(request, 'all_tasks.html', context)


@login_required
def task_detail(request, task_id):
    """Детальная страница задачи и ручная отправка на проверку преподавателю."""
    student = get_object_or_404(Student, user=request.user)
    task = get_object_or_404(Task, id=task_id)

    submission = Submission.objects.filter(student=student, task=task).first()

    # Если задача еще не взята в работу
    if not submission:
        active_count = Submission.objects.filter(student=student, status='IN_PROGRESS').count()
        if active_count >= 2:
            messages.warning(request, 'Вы не можете взять более 2 задач одновременно. Завершите текущие задачи!')
            return redirect('all_tasks')
        submission = Submission.objects.create(student=student, task=task, status='IN_PROGRESS')

    cooldown = _cooldown_remaining(submission)

    # ОБРАБОТКА ОТПРАВКИ КОДА (Ручная проверка) — не чаще одного раза в 24 часа
    if request.method == 'POST':
        if cooldown:
            hours_left = int(cooldown.total_seconds() // 3600) + 1
            messages.warning(request, f'Эту задачу можно отправлять раз в 24 часа. Попробуйте снова через {hours_left} ч.')
            return redirect('task_detail', task_id=task.id)

        code = request.POST.get('code', '')
        submission.code = code
        submission.status = 'TESTING'  # Отправляем на проверку преподавателю
        submission.last_submitted_at = timezone.now()
        submission.save()

        messages.success(request, 'Код отправлен! Преподаватель проверит ваше решение и выставит балл.')
        return redirect('task_detail', task_id=task.id)

    context = {
        'task': task,
        'submission': submission,
        'cooldown_hours': int(cooldown.total_seconds() // 3600) + 1 if cooldown else None,
    }
    return render(request, 'task_detail.html', context)


@login_required
def kanban_board(request):
    """Главная страница ученика: Kanban доска."""
    if request.user.is_staff:
        return redirect('teacher_dashboard')
    student = get_object_or_404(Student, user=request.user)

    all_tasks = Task.objects.all()
    submissions = Submission.objects.filter(student=student)

    in_progress = submissions.filter(status='IN_PROGRESS')
    testing = submissions.filter(status='TESTING')
    done = submissions.filter(status='DONE')

    active_task_ids = submissions.values_list('task_id', flat=True)
    backlog_tasks = all_tasks.exclude(id__in=active_task_ids)

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

        messages.success(request, 'Код отправлен на проверку преподавателю!')

    return redirect('kanban')


@login_required
def profile_view(request):
    """Профиль ученика с расчетом среднего балла."""
    student = get_object_or_404(Student, user=request.user)

    solved_submissions = Submission.objects.filter(student=student, status='DONE').order_by('-updated_at')

    solved_count = solved_submissions.count()
    total_score = sum(sub.score for sub in solved_submissions)

    average_score = round(total_score / solved_count, 1) if solved_count > 0 else 0

    context = {
        'student': student,
        'solved_submissions': solved_submissions,
        'solved_count': solved_count,
        'total_score': total_score,
        'average_score': average_score,
    }
    return render(request, 'profile.html', context)


def leaderboard(request):
    """Лидерборд: Топ-10 учеников с общим и средним баллом."""
    # Используем submissions__score, как было в ваших моделях
    students_query = Student.objects.annotate(
        total_score=Sum('submissions__score', filter=Q(submissions__status='DONE')),
        solved_count=Count('submissions', filter=Q(submissions__status='DONE'))
    ).order_by('-total_score', '-solved_count')[:10]

    students = []
    for student in students_query:
        total = student.total_score or 0
        solved = student.solved_count or 0
        avg_score = round(total / solved, 1) if solved > 0 else 0

        student.calc_total_score = total
        student.calc_solved_count = solved
        student.calc_average_score = avg_score
        students.append(student)

    return render(request, 'leaderboard.html', {'students': students})


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
        return redirect('all_tasks')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            auth_login(request, user)
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('all_tasks')
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
    """Таблица по темам курса: средний балл по классам (не решил — 0 баллов)."""
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

    topics = course.topics.prefetch_related('tasks').order_by('order', 'name')

    # Ученики по классам (только релевантные классы)
    students_by_class = {}
    for cls in classes:
        students_by_class[cls] = list(Student.objects.filter(school_class=cls))

    # Все баллы DONE-решений одним запросом: {(student_id, task_id): score}
    scores = {
        (s['student_id'], s['task_id']): s['score']
        for s in Submission.objects.filter(status='DONE').values('student_id', 'task_id', 'score')
    }

    rows = []
    for topic in topics:
        task_ids = list(topic.tasks.values_list('id', flat=True))
        cells = []
        for cls in classes:
            students = students_by_class.get(cls, [])
            total_possible = len(students) * len(task_ids)
            total_score = 0
            done_count = 0
            for student in students:
                for task_id in task_ids:
                    score = scores.get((student.id, task_id))
                    if score is not None:
                        total_score += score
                        done_count += 1
            average = round(total_score / total_possible, 1) if total_possible > 0 else 0
            cells.append({
                'class_name': cls,
                'average': average,
                'done_count': done_count,
                'total_possible': total_possible,
            })
        rows.append({
            'topic': topic,
            'task_count': len(task_ids),
            'cells': cells,
        })

    context = {
        'course': course,
        'classes': classes,
        'rows': rows,
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
    students_qs = Student.objects.all()
    my_courses = Course.objects.all()

    if teacher is not None:
        # Учитель видит только очередь и статистику по своим классам и курсам.
        class_list = teacher.class_list()
        pending_submissions = pending_submissions.filter(student__school_class__in=class_list)
        graded_submissions = graded_submissions.filter(student__school_class__in=class_list)
        students_qs = students_qs.filter(school_class__in=class_list)
        my_courses = teacher.courses.all()

    pending_submissions = pending_submissions.order_by('updated_at')
    graded_submissions = graded_submissions.order_by('-updated_at')[:20]

    # Лидерборд (Топ-10) — по всем ученикам для админа, по своим классам для учителя
    top_students = students_qs.annotate(
        total_score=Sum('submissions__score', filter=Q(submissions__status='DONE')),
        solved_count=Count('submissions', filter=Q(submissions__status='DONE'))
    ).order_by('-total_score')[:10]

    context = {
        'pending_submissions': pending_submissions,
        'graded_submissions': graded_submissions,
        'top_students': top_students,
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

    return render(request, 'grade_submission.html', {'submission': submission})