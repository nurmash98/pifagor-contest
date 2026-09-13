from django.contrib import admin
from .models import Student, Task, Submission

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'level')
    list_filter = ('level',)
    search_fields = ('title', 'description')

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