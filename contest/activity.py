"""Активность учеников за период: сколько НОВЫХ задач каждый сделал.

Задача считается, если первая отправка по ней пришла внутри периода. Если ученик отправлял её
раньше, а в периоде отправил снова: балл стал БОЛЬШЕ — задача считается, балл такой же или меньше —
это пересдача, в «новые» не идёт (показывается отдельно).
«Решено» — итоговый балл 10, как и в рейтинге; «частично» — проверено, но меньше 10."""
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Max, Min
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from .models import Student, Submission, Tag, Task, normalize_school_class
from .names import name_key

PERIODS = [('1h', '1 час', timedelta(hours=1)), ('2h', '2 часа', timedelta(hours=2)),
           ('1d', 'День', timedelta(days=1)), ('2d', '2 дня', timedelta(days=2)),
           ('3d', '3 дня', timedelta(days=3)), ('4d', '4 дня', timedelta(days=4)),
           ('7d', 'До 7 дней', timedelta(days=7))]
DEFAULT_PERIOD = '1d'
LEVELS = [k for k, _ in Task.LEVEL_CHOICES]


def activity_rows(students, start, levels, tag_id=None):
    """[{'student', 'solved', 'partial', 'pending', 'new', 'repeats', 'tasks'}] для списка учеников.
    new = solved + partial + pending (задачи, засчитанные в периоде)."""
    from .models import Attempt
    rows = {s.id: {'student': s, 'solved': 0, 'partial': 0, 'pending': 0, 'repeats': 0, 'tasks': []}
            for s in students}
    qs = Submission.objects.filter(student__in=list(rows), task__level__in=levels)
    if tag_id:
        qs = qs.filter(task_id__in=Task.objects.filter(tags__id=tag_id).values('id'))
    subs = {s.id: s for s in qs.select_related('task')}
    info = {}      # submission_id -> {'first', 'before_best', 'win_best', 'win_any'}
    for a in (Attempt.objects.filter(submission_id__in=list(subs)).values('submission_id', 'submitted_at', 'score')):
        i = info.setdefault(a['submission_id'], {'first': a['submitted_at'], 'before_best': 0, 'win_best': None, 'win_any': False})
        if a['submitted_at'] < i['first']:
            i['first'] = a['submitted_at']
        if a['submitted_at'] < start:
            i['before_best'] = max(i['before_best'], a['score'] or 0)
        else:
            i['win_any'] = True
            if a['score'] is not None:
                i['win_best'] = a['score'] if i['win_best'] is None else max(i['win_best'], a['score'])
    for sid, sub in subs.items():
        i = info.get(sid)
        if i is None:        # старая запись без попыток: берём даты самой отправки
            first = sub.created_at
            i = {'first': first, 'before_best': 0, 'win_best': sub.score if sub.is_graded else None,
                 'win_any': (sub.last_submitted_at or first) >= start}
            if first < start:
                i['before_best'] = sub.score if sub.is_graded else 0
        if not i['win_any']:
            continue
        row = rows[sub.student_id]
        if i['first'] >= start:                       # первая отправка в периоде — новая задача
            score = i['win_best']
        elif i['win_best'] is not None and i['win_best'] > i['before_best']:
            score = i['win_best']                     # раньше отправлял, но балл стал БОЛЬШЕ — считаем
        else:
            row['repeats'] += 1                       # балл такой же или меньше (или ещё не проверено)
            continue
        kind = 'pending' if score is None else ('solved' if score == 10 else 'partial')
        row[kind] += 1
        row['tasks'].append({'task': sub.task, 'kind': kind, 'score': score, 'at': i['first'],
                             'improved': i['first'] < start, 'was': i['before_best']})
    out = list(rows.values())
    for r in out:
        r['new'] = r['solved'] + r['partial'] + r['pending']
        r['tasks'].sort(key=lambda t: t['at'])
    out.sort(key=lambda r: (r['student'].school_class or '', name_key(r['student'].full_name)))
    return out


@staff_member_required
def student_activity(request):
    from .views import _managed_classes
    classes = _managed_classes(request.user)
    students = Student.objects.select_related('user')
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


@staff_member_required
def edupage_names(request):
    """Приводит имена учеников к виду из Edupage («Фамилия Имя»): для каждого ученика класса предлагаем
    самое похожее имя из списка Edupage, учитель проверяет и сохраняет."""
    from django.contrib import messages
    from django.shortcuts import redirect
    from .edupage_roster import ROSTER
    from .names import best_matches, name_key, sort_students
    from .views import _managed_classes
    classes = _managed_classes(request.user)
    available = [c for c in ROSTER if classes is None or c in classes]
    cls = request.GET.get('class') or request.POST.get('class') or (available[0] if available else '')
    if cls not in available:
        return render(request, 'edupage_names.html', {'available': available, 'cls': '', 'rows': []})
    roster = ROSTER[cls]
    students = sort_students(Student.objects.filter(school_class=cls))

    if request.method == 'POST':
        changed = 0
        taken = set()
        for st in students:
            new = request.POST.get(f'name_{st.id}', '')
            if not new or new not in roster or new == st.full_name:
                continue
            if new in taken:
                messages.error(request, f'Имя «{new}» выбрано для двух учеников — второе пропущено.')
                continue
            taken.add(new)
            st.full_name = new
            st.save(update_fields=['full_name'])
            changed += 1
        messages.success(request, f'Класс {cls}: имён обновлено — {changed}.')
        return redirect(f"{request.path}?class={cls}")

    matches = best_matches([s.full_name for s in students], roster)
    rows, used = [], set()
    for i, st in enumerate(students):
        j, score = matches.get(i, (None, 0))
        guess = roster[j] if j is not None else ''
        if guess:
            used.add(guess)
        rows.append({'student': st, 'guess': guess, 'same': guess == st.full_name, 'score': round(score * 100)})
    rows.sort(key=lambda r: name_key(r['guess'] or r['student'].full_name))
    return render(request, 'edupage_names.html', {
        'available': available, 'cls': cls, 'rows': rows, 'roster': sorted(roster, key=name_key),
        'missing': [n for n in sorted(roster, key=name_key) if n not in used],
    })
