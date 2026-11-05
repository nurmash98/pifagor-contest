"""Активность учеников за период: сколько НОВЫХ задач каждый сделал.

Задача считается, только если ПЕРВАЯ отправка по ней пришла внутри периода. Если ученик уже
отправлял её раньше, а теперь отправил ещё раз ради большего балла — это пересдача: она не
идёт в «новые» (показывается отдельно, чтобы учитель видел, что ничего не потеряно).
«Решено» — итоговый балл 10, как и в рейтинге; «частично» — проверено, но меньше 10."""
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Max, Min
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from .models import Student, Submission, Tag, Task, normalize_school_class

PERIODS = [('1h', '1 час', timedelta(hours=1)), ('2h', '2 часа', timedelta(hours=2)),
           ('1d', 'День', timedelta(days=1)), ('2d', '2 дня', timedelta(days=2)),
           ('3d', '3 дня', timedelta(days=3)), ('4d', '4 дня', timedelta(days=4)),
           ('7d', 'До 7 дней', timedelta(days=7))]
DEFAULT_PERIOD = '1d'
LEVELS = [k for k, _ in Task.LEVEL_CHOICES]


def activity_rows(students, start, levels, tag_id=None):
    """[{'student', 'solved', 'partial', 'pending', 'new', 'repeats', 'tasks'}] для списка учеников.
    new = solved + partial + pending (все впервые отправленные в периоде задачи)."""
    rows = {s.id: {'student': s, 'solved': 0, 'partial': 0, 'pending': 0, 'repeats': 0, 'tasks': []}
            for s in students}
    qs = Submission.objects.filter(student__in=list(rows), task__level__in=levels)
    if tag_id:
        qs = qs.filter(task_id__in=Task.objects.filter(tags__id=tag_id).values('id'))
    subs = (qs
            .annotate(first=Coalesce(Min('attempts__submitted_at'), 'created_at'),
                      last=Coalesce(Max('attempts__submitted_at'), 'last_submitted_at', 'created_at'))
            .filter(last__gte=start).select_related('task'))
    for sub in subs:
        row = rows[sub.student_id]
        if sub.first < start:
            row['repeats'] += 1          # уже отправлял раньше — пересдача, не считаем
            continue
        if not sub.is_graded:
            kind = 'pending'
        elif sub.score == 10:
            kind = 'solved'
        else:
            kind = 'partial'
        row[kind] += 1
        row['tasks'].append({'task': sub.task, 'kind': kind, 'score': sub.score, 'at': sub.first})
    out = list(rows.values())
    for r in out:
        r['new'] = r['solved'] + r['partial'] + r['pending']
        r['tasks'].sort(key=lambda t: t['at'])
    out.sort(key=lambda r: (-r['solved'], -r['new'], r['student'].school_class, r['student'].full_name))
    return out


@staff_member_required
def student_activity(request):
    from .views import _managed_classes
    classes = _managed_classes(request.user)
    students = Student.objects.select_related('user').order_by('school_class', 'full_name')
    if classes is not None:
        students = students.filter(school_class__in=classes)
        all_classes = sorted(set(classes))
    else:
        all_classes = list(Student.objects.order_by('school_class').values_list('school_class', flat=True).distinct())
    class_filter = normalize_school_class(request.GET.get('class', ''))
    if class_filter:
        students = students.filter(school_class=class_filter)

    period = request.GET.get('period', DEFAULT_PERIOD)
    delta = {k: d for k, _, d in PERIODS}.get(period)
    if delta is None:
        period, delta = DEFAULT_PERIOD, dict((k, d) for k, _, d in PERIODS)[DEFAULT_PERIOD]
    picked = [l for l in request.GET.getlist('level') if l in LEVELS]
    levels = picked or LEVELS
    tag_raw = request.GET.get('tag', '')
    tag_id = int(tag_raw) if tag_raw.isdigit() else None
    now = timezone.now()
    start = now - delta
    rows = activity_rows(list(students), start, levels, tag_id)
    totals = {k: sum(r[k] for r in rows) for k in ('solved', 'partial', 'pending', 'new', 'repeats')}
    return render(request, 'student_activity.html', {
        'rows': rows, 'totals': totals, 'periods': [(k, label) for k, label, _ in PERIODS], 'period': period,
        'all_classes': all_classes, 'class_filter': class_filter, 'levels': Task.LEVEL_CHOICES,
        'picked_levels': levels, 'tags': Tag.objects.all(), 'tag_id': tag_id, 'start': start, 'my_classes': classes,
    })
