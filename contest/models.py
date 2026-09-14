from django.db import models
from django.contrib.auth.models import User


class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150, verbose_name="Имя и Фамилия")
    school_class = models.CharField(max_length=10, verbose_name="Класс (например, 10А)")

    def __str__(self):
        return f"{self.full_name} ({self.school_class})"


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="Название тега")

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"
        ordering = ['name']

    def __str__(self):
        return self.name


class Task(models.Model):
    LEVEL_CHOICES = [
        ('A', 'Уровень A (Легкий)'),
        ('B', 'Уровень B (Средний)'),
        ('C', 'Уровень C (Сложный)'),
    ]
    test_cases = models.JSONField(default=list, help_text="Список словарей [{'input': '...', 'output': '...'}]")
    title = models.CharField(max_length=200, verbose_name="Название задачи")
    level = models.CharField(max_length=1, choices=LEVEL_CHOICES, verbose_name="Уровень")
    description = models.TextField(verbose_name="Описание")
    input_example = models.TextField(verbose_name="Пример ввода")
    output_example = models.TextField(verbose_name="Пример вывода")
    tags = models.ManyToManyField(Tag, related_name='tasks', verbose_name="Теги")

    def __str__(self):
        return f"[{self.level}] {self.title}"


class Course(models.Model):
    name = models.CharField(max_length=200, verbose_name="Название курса")
    description = models.TextField(blank=True, verbose_name="Описание")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Курс"
        verbose_name_plural = "Курсы"
        ordering = ['name']

    def __str__(self):
        return self.name


class Topic(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='topics', verbose_name="Курс")
    name = models.CharField(max_length=200, verbose_name="Название темы")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок отображения")
    tasks = models.ManyToManyField(Task, blank=True, related_name='topics', verbose_name="Задачи")

    class Meta:
        verbose_name = "Тема"
        verbose_name_plural = "Темы"
        ordering = ['course', 'order', 'name']

    def __str__(self):
        return f"{self.course.name} — {self.name}"





class Submission(models.Model):
    STATUS_CHOICES = [
        ('TODO', 'В Бэклоге'),
        ('IN_PROGRESS', 'В работе'),
        ('TESTING', 'На проверке'),
        ('DONE', 'Решено'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='submissions')
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='TODO')
    code = models.TextField(blank=True, null=True, verbose_name="Код ученика")
    score = models.IntegerField(default=0, verbose_name="Баллы (0-10)")
    teacher_comment = models.TextField(blank=True, null=True, help_text="Комментарий преподавателя")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.full_name} - {self.task.title} ({self.status})"