import re

from django.db import models
from django.contrib.auth.models import User, Group


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
    test_cases = models.JSONField(
        default=list, blank=True,
        help_text="Список словарей [{'input': '...', 'output': '...'}] — необязательно, можно добавить позже"
    )
    title = models.CharField(max_length=200, verbose_name="Название задачи")
    level = models.CharField(max_length=1, choices=LEVEL_CHOICES, verbose_name="Уровень")
    description = models.TextField(verbose_name="Описание")
    input_example = models.TextField(verbose_name="Пример ввода")
    output_example = models.TextField(verbose_name="Пример вывода")
    tags = models.ManyToManyField(Tag, related_name='tasks', verbose_name="Теги")

    def __str__(self):
        label = f"[{self.level}] {self.title}"
        if self.pk:
            tag_names = ", ".join(self.tags.values_list('name', flat=True))
            if tag_names:
                label = f"{label} ({tag_names})"
        return label


class Course(models.Model):
    name = models.CharField(max_length=200, verbose_name="Название курса")
    description = models.TextField(blank=True, verbose_name="Описание")
    is_visible = models.BooleanField(default=False, verbose_name="Видно ученикам",
                                      help_text="Пока не включено — курс виден только учителям/админу")
    created_by = models.ForeignKey('Teacher', null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='created_courses', verbose_name="Кем создан")
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
    theory_video_url = models.URLField(
        blank=True, verbose_name="Видеоурок (ссылка на YouTube)",
        help_text="Необязательно. Ученик сможет открыть видео отдельной страницей."
    )
    tasks = models.ManyToManyField(Task, blank=True, related_name='topics', verbose_name="Задачи")

    class Meta:
        verbose_name = "Тема"
        verbose_name_plural = "Темы"
        ordering = ['course', 'order', 'name']

    def __str__(self):
        return f"{self.course.name} — {self.name}"

    def youtube_embed_url(self):
        """URL для встраивания ролика (<iframe>), если ссылка похожа на YouTube."""
        if not self.theory_video_url:
            return None
        match = re.search(r'(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})', self.theory_video_url)
        return f'https://www.youtube.com/embed/{match.group(1)}' if match else None


class Teacher(models.Model):
    """Профиль учителя. Создаётся только админом (суперпользователем) в Django admin —
    сами учителя не могут регистрироваться и назначать других учителей."""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150, verbose_name="Имя и Фамилия")
    classes = models.CharField(
        max_length=255, blank=True,
        verbose_name="Классы",
        help_text="Классы этого учителя через запятую, например: 10А, 11Б"
    )
    courses = models.ManyToManyField(Course, blank=True, related_name='teachers', verbose_name="Курсы")

    class Meta:
        verbose_name = "Учитель"
        verbose_name_plural = "Учителя"

    def __str__(self):
        return self.full_name

    def class_list(self):
        """Список классов учителя без пробелов, например ['10А', '11Б']."""
        return [c.strip() for c in self.classes.split(',') if c.strip()]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Учитель автоматически получает доступ в админку и нужные права,
        # чтобы админу не пришлось настраивать это вручную каждый раз.
        if not self.user.is_staff:
            self.user.is_staff = True
            self.user.save(update_fields=['is_staff'])
        teachers_group, _ = Group.objects.get_or_create(name='Teachers')
        self.user.groups.add(teachers_group)





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
    last_submitted_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Последняя отправка на проверку"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.full_name} - {self.task.title} ({self.status})"