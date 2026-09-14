from django.contrib import admin
from .models import Student, Task, Submission, Tag, Course, Topic


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
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


class TopicInline(admin.TabularInline):
    """Позволяет создавать темы курса прямо со страницы курса.
    Выбор задач для темы — на отдельной странице темы (кнопка 'Изменить')."""
    model = Topic
    extra = 1
    fields = ('name', 'order')
    show_change_link = True


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'topics_count', 'created_at')
    search_fields = ('name',)
    inlines = [TopicInline]

    @admin.display(description="Тем в курсе")
    def topics_count(self, obj):
        return obj.topics.count()


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'course', 'tasks_count', 'order')
    list_filter = ('course',)
    search_fields = ('name', 'course__name')
    filter_horizontal = ('tasks',)

    @admin.display(description="Задач в теме")
    def tasks_count(self, obj):
        return obj.tasks.count()


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'school_class')
    search_fields = ('full_name', 'school_class')

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('student', 'task', 'status', 'score', 'updated_at')
    list_filter = ('status', 'task__level')
    search_fields = ('student__full_name', 'task__title')
    readonly_fields = ('created_at', 'updated_at')