from django.urls import path
from . import views

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
    path('profile/', views.profile_view, name='profile'),

    # Авторизация и регистрация
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

# Кабинет преподавателя
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('teacher/grade/<int:submission_id>/', views.grade_submission, name='grade_submission'),
]