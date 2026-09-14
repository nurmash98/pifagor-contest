from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib import messages

from .models import Task, Submission, Student
from .forms import StudentRegistrationForm
from django.contrib.admin.views.decorators import staff_member_required


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

    # ОБРАБОТКА ОТПРАВКИ КОДА (Ручная проверка)
    if request.method == 'POST':
        code = request.POST.get('code', '')
        submission.code = code
        submission.status = 'TESTING'  # Отправляем на проверку админу
        submission.save()

        messages.success(request, 'Код отправлен! Преподаватель проверит ваше решение и выставит балл.')
        return redirect('kanban')

    context = {
        'task': task,
        'submission': submission,
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
        code = request.POST.get('code', '')

        submission.code = code
        submission.status = 'TESTING'
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


@staff_member_required
def teacher_dashboard(request):
    """Кабинет преподавателя: очередь на проверку и последние проверенные."""
    # Задачи, ожидающие проверки
    pending_submissions = Submission.objects.filter(status='TESTING').order_by('updated_at')

    # Уже проверенные задачи (последние 20)
    graded_submissions = Submission.objects.filter(status='DONE').order_by('-updated_at')[:20]

    # Лидерборд для учителя (Топ-10)
    top_students = Student.objects.annotate(
        total_score=Sum('submissions__score', filter=Q(submissions__status='DONE')),
        solved_count=Count('submissions', filter=Q(submissions__status='DONE'))
    ).order_by('-total_score')[:10]

    context = {
        'pending_submissions': pending_submissions,
        'graded_submissions': graded_submissions,
        'top_students': top_students,
    }
    return render(request, 'teacher_dashboard.html', context)


@staff_member_required
def grade_submission(request, submission_id):
    """Страница проверки конкретного решения."""
    submission = get_object_or_404(Submission, id=submission_id)

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