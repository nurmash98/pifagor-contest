from django.contrib import admin
from .models import Student, Task, Submission, Attempt, Tag, Course, Topic, Teacher, ClassBonus


def _teacher_of(request):
    """Профиль учителя текущего пользователя, если это учитель (не админ)."""
    if request.user.is_superuser:
        return None
    return Teacher.objects.filter(user=request.user).first()


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    # Полный доступ (создать/изменить/удалить) есть и у учителей — им нужно
    # добавлять и снимать теги с задач.
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'level', 'get_tags')
    list_filter = ('level', 'tags')
    search_fields = ('title', 'description')
    filter_horizontal = ('tags',)

    @admin.display(description="Теги")
    def get_tags(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all()) or "—"

    def get_readonly_fields(self, request, obj=None):
        # У учителей (не у админа) есть доступ только к тегам задачи —
        # остальные поля задачи защищены от изменений.
        if request.user.is_superuser:
            return ()
        return ('title', 'level', 'description', 'input_example', 'output_example', 'test_cases')


class TopicInline(admin.TabularInline):
    """Позволяет создавать темы курса прямо со страницы курса.
    Выбор задач для темы — на отдельной странице темы (кнопка 'Изменить')."""
    model = Topic
    extra = 1
    fields = ('name', 'order', 'theory_video_url')
    show_change_link = True


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_visible', 'topics_count', 'created_by', 'created_at')
    list_filter = ('is_visible',)
    search_fields = ('name',)
    inlines = [TopicInline]

    @admin.display(description="Тем в курсе")
    def topics_count(self, obj):
        return obj.topics.count()

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        teacher = _teacher_of(request)
        if teacher is None:
            return qs
        # Учитель видит и редактирует только свои курсы (созданные им или назначенные админом).
        return qs.filter(teachers=teacher)

    def get_exclude(self, request, obj=None):
        if request.user.is_superuser:
            return ()
        # Учитель не назначает курс себе/другим вручную — это происходит автоматически.
        return ('created_by', 'teachers')

    def save_model(self, request, obj, form, change):
        teacher = _teacher_of(request)
        if not change and teacher is not None:
            obj.created_by = teacher
        super().save_model(request, obj, form, change)
        if not change and teacher is not None:
            obj.teachers.add(teacher)


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'course', 'tasks_count', 'order')
    list_filter = ('course',)
    search_fields = ('name', 'course__name')
    filter_horizontal = ('tasks',)

    @admin.display(description="Задач в теме")
    def tasks_count(self, obj):
        return obj.tasks.count()

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        teacher = _teacher_of(request)
        if teacher is None:
            return qs
        return qs.filter(course__teachers=teacher)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'course':
            teacher = _teacher_of(request)
            if teacher is not None:
                kwargs['queryset'] = Course.objects.filter(teachers=teacher)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    # Доступно только админу: только у него есть право add/change/delete Teacher.
    list_display = ('full_name', 'user', 'classes')
    search_fields = ('full_name', 'user__username', 'classes')
    filter_horizontal = ('courses',)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'school_class')
    search_fields = ('full_name', 'school_class')

class AttemptInline(admin.TabularInline):
    """История попыток по задаче (только просмотр — оценки ставятся на странице проверки)."""
    model = Attempt
    extra = 0
    can_delete = False
    fields = ('number', 'submitted_at', 'score', 'teacher_comment', 'graded_at')
    readonly_fields = fields


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    inlines = [AttemptInline]
    list_display = ('student', 'task', 'status', 'score', 'autotest_passed', 'autotest_total', 'updated_at')
    list_filter = ('status', 'task__level')
    search_fields = ('student__full_name', 'task__title')
    readonly_fields = ('created_at', 'updated_at', 'autotest_passed', 'autotest_total',
                       'autotest_results', 'autotest_checked_at')


@admin.register(ClassBonus)
class ClassBonusAdmin(admin.ModelAdmin):
    # В основном ставится кнопками на странице лидерборда — здесь просмотр/удаление
    # для админа (например, если учитель ошибся или нужно всё почистить).
    list_display = ('school_class', 'points', 'given_by', 'comment', 'created_at')
    list_filter = ('school_class',)
    search_fields = ('school_class', 'comment')
    readonly_fields = ('created_at',)