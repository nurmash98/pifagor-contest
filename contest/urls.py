from django.urls import path
from . import views
from . import exam_views

urlpatterns = [
    # Главная страница - каталог задач
    path('', views.all_tasks_view, name='all_tasks'),

    # Детальная страница задачи и отправка кода
    path('task/<int:task_id>/', views.task_detail, name='task_detail'),
    path('task/<int:task_id>/take/', views.take_task, name='take_task'),

    # Доска задач и быстрая отправка кода (если используется)
    path('kanban/', views.kanban_board, name='kanban'),
    path('submission/<int:submission_id>/submit/', views.submit_code, name='submit_code'),

    # Рейтинг и профиль
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('leaderboard/class-bonus/', views.add_class_bonus, name='add_class_bonus'),
    path('profile/', views.profile_view, name='profile'),

    # Курсы
    path('courses/', views.courses_view, name='courses'),
    path('courses/<int:course_id>/', views.course_detail, name='course_detail'),
    path('courses/topics/<int:topic_id>/video/', views.topic_video, name='topic_video'),
    path('courses/topics/<int:topic_id>/theory/', views.topic_theory, name='topic_theory'),

    # Авторизация и регистрация
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Переключатель языка условий задач (RU/KZ)
    path('lang/<str:lang>/', views.set_content_lang, name='set_content_lang'),

    # СОР / СОЧ (БЖБ / ТЖБ)
    path('exams/', exam_views.exam_list, name='exam_list'),
    path('exams/new/', exam_views.exam_create, name='exam_create'),
    path('exams/<int:exam_id>/', exam_views.exam_detail, name='exam_detail'),
    path('exams/<int:exam_id>/start/', exam_views.exam_start, name='exam_start'),
    path('exams/<int:exam_id>/finish/', exam_views.exam_finish, name='exam_finish'),
    path('exams/<int:exam_id>/task/<int:task_id>/', exam_views.exam_task, name='exam_task'),
    path('exams/<int:exam_id>/edit/', exam_views.exam_edit, name='exam_edit'),
    path('exams/<int:exam_id>/delete/', exam_views.exam_delete, name='exam_delete'),
    path('exams/<int:exam_id>/results/', exam_views.exam_results, name='exam_results'),
    # Пересдача СОР/СОЧ
    path('exams/<int:exam_id>/retake/new/', exam_views.retake_create, name='retake_create'),
    path('exams/<int:exam_id>/retake/<int:retake_id>/', exam_views.exam_detail, name='retake_detail'),
    path('exams/<int:exam_id>/retake/<int:retake_id>/start/', exam_views.exam_start, name='retake_start'),
    path('exams/<int:exam_id>/retake/<int:retake_id>/finish/', exam_views.exam_finish, name='retake_finish'),
    path('exams/<int:exam_id>/retake/<int:retake_id>/task/<int:task_id>/', exam_views.exam_task, name='retake_task'),
    path('exams/<int:exam_id>/retake/<int:retake_id>/results/', exam_views.exam_results, name='retake_results'),
    path('exams/<int:exam_id>/retake/<int:retake_id>/delete/', exam_views.retake_delete, name='retake_delete'),

    # Кабинет преподавателя
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('teacher/grade/<int:submission_id>/', views.grade_submission, name='grade_submission'),

    # Управление курсами учителем (без Django admin)
    path('teacher/courses/create/', views.course_create, name='course_create'),
    path('teacher/courses/<int:course_id>/manage/', views.course_manage, name='course_manage'),
    path('teacher/courses/<int:course_id>/edit/', views.course_edit, name='course_edit'),
    path('teacher/courses/<int:course_id>/analytics/', views.course_analytics, name='course_analytics'),
    path('teacher/courses/<int:course_id>/topics/add/', views.topic_create, name='topic_create'),
    path('teacher/topics/<int:topic_id>/edit/', views.topic_edit, name='topic_edit'),

    # Задачи: любой учитель может редактировать любую задачу (условие, пример, тесты, теги)
    path('teacher/tasks/', views.task_tags_list, name='task_tags_list'),
    path('teacher/tasks/new/', views.task_create, name='task_create'),
    path('teacher/tasks/import/', views.task_import, name='task_import'),
    path('teacher/tasks/<int:task_id>/edit/', views.task_edit, name='task_edit'),
    path('teacher/tasks/<int:task_id>/tags/', views.task_tags_edit, name='task_tags_edit'),  # старый адрес

    # Управление учениками — доступно любому учителю для всех учеников
    path('teacher/students/', views.student_list, name='student_list'),
    path('teacher/students/create/', views.student_create, name='student_create'),
    path('teacher/students/<int:student_id>/edit/', views.student_edit, name='student_edit'),
    path('teacher/students/<int:student_id>/delete/', views.student_delete, name='student_delete'),
]