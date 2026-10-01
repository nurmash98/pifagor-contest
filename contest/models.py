import re

from django.db import models
from django.contrib.auth.models import User, Group


# Литер класса — всегда заглавная английская буква, слитно с цифрой: «7F».
# Русские/казахские буквы, похожие на английские, переводим ПО ВИДУ (В→B, С→C, Н→H, Р→P,
# Х→X, М→M, Т→T, К→K…), остальные — по звучанию (Ф→F, Д→D, Г→G, Л→L…).
CYRILLIC_CLASS_LETTERS = {
    'А': 'A', 'В': 'B', 'Е': 'E', 'Ё': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O',
    'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X', 'У': 'U',
    'Б': 'B', 'Г': 'G', 'Д': 'D', 'З': 'Z', 'И': 'I', 'Й': 'I', 'Л': 'L', 'П': 'P',
    'Ф': 'F', 'Ц': 'C', 'Э': 'E', 'Ы': 'Y',
    'Ә': 'A', 'Ғ': 'G', 'Қ': 'K', 'Ң': 'N', 'Ө': 'O', 'Ұ': 'U', 'Ү': 'U', 'Һ': 'H', 'І': 'I',
}


def normalize_school_class(value):
    """'7f', '7 f', '7 F', '7ф', ' 7 Ф ' → '7F'. Пробелы убираются, буквы — заглавные
    английские. Буквы без понятной английской пары остаются как есть (заглавными)."""
    text = ''.join((value or '').split()).upper()
    return ''.join(CYRILLIC_CLASS_LETTERS.get(ch, ch) for ch in text)


class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150, verbose_name="Имя и Фамилия")
    school_class = models.CharField(max_length=10, verbose_name="Класс (например, 7F)")

    def save(self, *args, **kwargs):
        self.school_class = normalize_school_class(self.school_class)
        super().save(*args, **kwargs)

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

    # Казахская версия условия — необязательна. Если не заполнена, сайт
    # показывает русский текст (переключатель RU/KZ в шапке сайта).
    title_kk = models.CharField(max_length=200, blank=True, verbose_name="Атауы (қазақша)")
    description_kk = models.TextField(blank=True, verbose_name="Сипаттамасы (қазақша)")
    input_example_kk = models.TextField(blank=True, verbose_name="Кіріс мысалы (қазақша)")
    output_example_kk = models.TextField(blank=True, verbose_name="Шығыс мысалы (қазақша)")

    def get_display_title(self, lang='ru'):
        return self.title_kk if lang == 'kk' and self.title_kk else self.title

    def get_display_description(self, lang='ru'):
        return self.description_kk if lang == 'kk' and self.description_kk else self.description

    def get_display_input_example(self, lang='ru'):
        return self.input_example_kk if lang == 'kk' and self.input_example_kk else self.input_example

    def get_display_output_example(self, lang='ru'):
        return self.output_example_kk if lang == 'kk' and self.output_example_kk else self.output_example

    class Meta:
        # Задачи по умолчанию отсортированы по уровню (легкие → сложные), затем по названию.
        # Это автоматически сортирует задачи ВЕЗДЕ, где они выводятся без своего явного
        # order_by — каталог задач, список задач внутри темы курса и т.п.
        ordering = ['level', 'title']

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
    # Теория — HTML-файл, который загружает учитель. Храним его содержимое прямо в базе
    # (а не файлом на диске): на Render диск стирается при каждом деплое.
    theory_html = models.TextField(blank=True, verbose_name="Теория (HTML)")
    theory_filename = models.CharField(max_length=255, blank=True, verbose_name="Имя файла теории")
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
        match = re.search(r'(?:v=|youtu\.be/|embed/|shorts/|live/)([A-Za-z0-9_-]{11})', self.theory_video_url)
        return f'https://www.youtube.com/embed/{match.group(1)}?rel=0' if match else None


class Teacher(models.Model):
    """Профиль учителя. Создаётся только админом (суперпользователем) в Django admin —
    сами учителя не могут регистрироваться и назначать других учителей."""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150, verbose_name="Имя и Фамилия")
    classes = models.CharField(
        max_length=255, blank=True,
        verbose_name="Классы",
        help_text="Классы этого учителя через запятую, например: 7F, 10A (пробелы, регистр и русские буквы исправятся сами)"
    )
    courses = models.ManyToManyField(Course, blank=True, related_name='teachers', verbose_name="Курсы")

    class Meta:
        verbose_name = "Учитель"
        verbose_name_plural = "Учителя"

    def __str__(self):
        return self.full_name

    def class_list(self):
        """Список классов учителя в стандартном виде, например ['7F', '10A']."""
        return [normalize_school_class(c) for c in self.classes.split(',') if c.strip()]

    def save(self, *args, **kwargs):
        self.classes = ', '.join(dict.fromkeys(self.class_list()))
        super().save(*args, **kwargs)
        # Учитель автоматически получает доступ в админку и нужные права,
        # чтобы админу не пришлось настраивать это вручную каждый раз.
        if not self.user.is_staff:
            self.user.is_staff = True
            self.user.save(update_fields=['is_staff'])
        teachers_group, _ = Group.objects.get_or_create(name='Teachers')
        self.user.groups.add(teachers_group)





class ClassBonus(models.Model):
    """Баллы, которые учитель вручную ставит целому классу — за атмосферу/вайб на уроке.
    Это НЕ про успеваемость (та считается по решённым задачам) — учитель ставит баллы
    просто когда ему хочется, без какой-либо строгой методики или обязательных условий."""
    school_class = models.CharField(max_length=10, verbose_name="Класс")
    points = models.IntegerField(verbose_name="Баллы")
    given_by = models.ForeignKey(
        'Teacher', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='class_bonuses', verbose_name="Кто поставил"
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Баллы классу"
        verbose_name_plural = "Баллы классам"
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        self.school_class = normalize_school_class(self.school_class)
        super().save(*args, **kwargs)

    def __str__(self):
        sign = '+' if self.points > 0 else ''
        return f"{self.school_class}: {sign}{self.points}"


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

    # Автоматический прогон кода ученика против Task.test_cases при отправке —
    # ПОДСКАЗКА ученику и преподавателю, не официальная оценка (её всё равно
    # ставит преподаватель вручную, см. score выше). Смотрите contest/autotest.py.
    autotest_passed = models.IntegerField(null=True, blank=True, verbose_name="Автотесты: пройдено")
    autotest_total = models.IntegerField(null=True, blank=True, verbose_name="Автотесты: всего")
    autotest_results = models.JSONField(
        default=list, blank=True,
        verbose_name="Автотесты: детали по каждому тесту",
        help_text="[{'ok': bool, 'error': None|'timeout'|'memory'|str, 'expected': str, 'actual': str}, ...]",
    )
    autotest_checked_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Когда прошёл последний прогон автотестов",
    )

    # Была ли хоть одна попытка проверена учителем. Нужна отдельно от status: когда ученик
    # отправляет новую попытку после проверки, status снова TESTING (чтобы учитель увидел её
    # в очереди), но лучшая оценка за прошлые попытки должна продолжать считаться в
    # лидерборде/профиле. score выше — это ИТОГОВАЯ оценка = максимум среди всех попыток.
    is_graded = models.BooleanField(default=False, verbose_name="Проверено хотя бы раз")

    def save(self, *args, **kwargs):
        if self.status == 'DONE':
            self.is_graded = True
        super().save(*args, **kwargs)

    def recalc_final_score(self):
        """Итоговая оценка = максимальная среди всех проверенных попыток."""
        best = self.attempts.filter(score__isnull=False).aggregate(best=models.Max('score'))['best']
        if best is not None:
            self.score = best
            self.is_graded = True
        return self.score

    def __str__(self):
        return f"{self.student.full_name} - {self.task.title} ({self.status})"


class Attempt(models.Model):
    """Одна отправка кода учеником по задаче (попытка) — с оценкой и комментарием учителя
    именно за эту попытку. Итоговая оценка по задаче (Submission.score) — лучшая из них."""
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='attempts')
    number = models.PositiveIntegerField(verbose_name="Номер попытки")
    code = models.TextField(blank=True, verbose_name="Код")
    submitted_at = models.DateTimeField(verbose_name="Отправлено")
    score = models.IntegerField(null=True, blank=True, verbose_name="Оценка (0-10)")
    teacher_comment = models.TextField(blank=True, verbose_name="Комментарий учителя")
    graded_at = models.DateTimeField(null=True, blank=True, verbose_name="Проверено")
    # Оценка выставлена автоматически по автотестам (а не учителем вручную).
    auto_graded = models.BooleanField(default=False, verbose_name="Оценено автоматически")
    tests_passed = models.IntegerField(null=True, blank=True, verbose_name="Тестов пройдено")
    tests_total = models.IntegerField(null=True, blank=True, verbose_name="Тестов всего")

    class Meta:
        verbose_name = "Попытка"
        verbose_name_plural = "Попытки"
        ordering = ['-number']

    @property
    def is_graded(self):
        return self.score is not None

    def __str__(self):
        return f"Попытка #{self.number} — {self.submission}"

class Exam(models.Model):
    """СОР / СОЧ (БЖБ / ТЖБ): учитель выбирает класс, время начала, длительность и задачи.
    Каждая задача — до 10 баллов (по автотестам), оценка за экзамен — средний балл по всем
    задачам экзамена. Сложность задач здесь не влияет на баллы."""
    KIND_CHOICES = [('SOR', 'СОР'), ('SOCH', 'СОЧ')]

    kind = models.CharField(max_length=4, choices=KIND_CHOICES, default='SOR', verbose_name="Тип")
    title = models.CharField(max_length=200, verbose_name="Название")
    school_class = models.CharField(max_length=10, verbose_name="Класс")
    start_at = models.DateTimeField(verbose_name="Начало")
    duration_minutes = models.PositiveIntegerField(verbose_name="Длительность (минут)")
    tasks = models.ManyToManyField(Task, related_name='exams', verbose_name="Задачи")
    # 0 — все ученики решают все выбранные задачи; N > 0 — каждому ученику при старте
    # компьютер случайно выбирает N задач из выбранных учителем (у соседей — разные варианты).
    random_count = models.PositiveIntegerField(default=0, verbose_name="Случайных задач каждому (0 — все)")
    # Случайный режим: условия для каждой задачи — [{"tag": id тега или null, "level": "A"/"B"/"C"/""}].
    # При старте каждому ученику для каждого условия выпадает своя случайная задача с тестами.
    random_slots = models.JSONField(default=list, blank=True, verbose_name="Условия случайных задач")
    created_by = models.ForeignKey('Teacher', null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='exams', verbose_name="Кто создал")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "СОР/СОЧ"
        verbose_name_plural = "СОР/СОЧ"
        ordering = ['-start_at']

    def save(self, *args, **kwargs):
        self.school_class = normalize_school_class(self.school_class)
        super().save(*args, **kwargs)

    @property
    def end_at(self):
        from datetime import timedelta
        return self.start_at + timedelta(minutes=self.duration_minutes)

    @property
    def is_random(self):
        return self.random_count > 0

    def slot_list(self):
        """Условия случайных задач с подписями: [{'tag': id, 'level': 'B', 'label': '#циклы · B'}]."""
        tag_names = dict(Tag.objects.filter(id__in=[sl.get('tag') for sl in self.random_slots if sl.get('tag')])
                         .values_list('id', 'name'))
        out = []
        for sl in self.random_slots:
            parts = []
            if sl.get('tag'):
                parts.append('#' + tag_names.get(sl['tag'], '?'))
            if sl.get('level'):
                parts.append(sl['level'])
            out.append({'tag': sl.get('tag'), 'level': sl.get('level') or '',
                        'label': ' · '.join(parts) or 'любая'})
        return out

    def tasks_per_student(self):
        return self.random_count if self.is_random else len(self.tasks.all())

    def status(self, now=None):
        """'upcoming' — ещё не началось, 'active' — идёт, 'finished' — время вышло."""
        from django.utils import timezone
        now = now or timezone.now()
        if now < self.start_at:
            return 'upcoming'
        return 'active' if now < self.end_at else 'finished'

    def __str__(self):
        return f"{self.get_kind_display()} «{self.title}» ({self.school_class})"


class ExamAttempt(models.Model):
    """Ученик нажал «Начать» на экзамене. Пока попытка идёт (не завершена и время экзамена
    не вышло), Курсы, Все задачи и Kanban для него закрыты."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='attempts')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='exam_attempts')
    started_at = models.DateTimeField(verbose_name="Начал")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершил досрочно")
    # Для СОР/СОЧ со случайными задачами — id задач, выпавших этому ученику (в порядке показа).
    task_ids = models.JSONField(default=list, blank=True, verbose_name="Выпавшие задачи")

    class Meta:
        verbose_name = "Попытка СОР/СОЧ"
        verbose_name_plural = "Попытки СОР/СОЧ"
        unique_together = [('exam', 'student')]

    def is_active(self, now=None):
        from django.utils import timezone
        now = now or timezone.now()
        return self.finished_at is None and now < self.exam.end_at

    def task_list(self):
        """Задачи этого ученика: выпавшие случайно, либо все задачи экзамена."""
        if self.exam.is_random and self.task_ids:
            by_id = {t.id: t for t in Task.objects.filter(id__in=self.task_ids)}
            return [by_id[i] for i in self.task_ids if i in by_id]
        return list(self.exam.tasks.all())

    def grade(self):
        """Оценка из 10 — средний балл по ВСЕМ задачам ученика (не сданная задача = 0)."""
        ids = {t.id for t in self.task_list()}
        task_count = len(ids)
        if not task_count:
            return 0
        total = sum(a.best_score for a in self.answers.all() if a.task_id in ids)
        return round(total / task_count, 1)

    def __str__(self):
        return f"{self.student} — {self.exam}"


class ExamAnswer(models.Model):
    """Решение одной задачи экзамена: последний отправленный код и лучший результат."""
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name='answers')
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    code = models.TextField(blank=True, verbose_name="Последний код")
    best_code = models.TextField(blank=True, verbose_name="Код с лучшим результатом")
    best_score = models.IntegerField(default=0, verbose_name="Лучший балл (0-10)")
    tests_passed = models.IntegerField(default=0)
    tests_total = models.IntegerField(default=0)
    results = models.JSONField(default=list, blank=True)
    submissions_count = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Ответ на СОР/СОЧ"
        verbose_name_plural = "Ответы на СОР/СОЧ"
        unique_together = [('attempt', 'task')]
