"""Аналитика для админа: заходы, СОР/СОЧ, средние баллы, решённые задачи — с сентября, по параллелям."""
from collections import defaultdict
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render
from django.utils import timezone

from .models import Exam, ExamAttempt, LoginEvent, Student, Submission
from .quarters import grade_of

SINCE = timezone.make_aware(datetime(2026, 9, 1))     # начало учебного года
BANDS = [('8–10', 8, 11, '#16a34a'), ('6–7', 6, 8, '#eab308'), ('4–5', 4, 6, '#f97316'), ('0–3', 0, 4, '#dc2626')]


def _num(v):
    return round(v, 1) if v is not None else None


def _avg(values):
    values = list(values)
    return round(sum(values) / len(values), 1) if values else None


def _parallel_key(p):
    return (0, int(p)) if str(p).isdigit() else (1, str(p))


def _week_start(dt):
    d = timezone.localtime(dt).date()
    return d - timedelta(days=d.weekday())


def _weeks(now):
    """Понедельники от начала года до сегодня."""
    d = _week_start(SINCE)
    last = _week_start(now)
    out = []
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def attempt_grade(attempt, answers_by_attempt, tasks_n):
    """Оценка из 10: сумма лучших баллов / число задач (не сданная = 0). Как ExamAttempt.grade()."""
    if not tasks_n:
        return 0.0
    total = sum(a.best_score for a in answers_by_attempt.get(attempt.id, []))
    return round(total / tasks_n, 1)


def build(parallel=None, now=None):
    """Все цифры дашборда. parallel — '7' (тогда группы = классы параллели) или None (группы = параллели)."""
    now = now or timezone.now()
    students = list(Student.objects.select_related('user'))
    for s in students:
        s.par = grade_of(s.school_class)
    pars = sorted({s.par for s in students if s.par}, key=_parallel_key)
    if parallel not in pars:
        parallel = None
    if parallel:
        students = [s for s in students if s.par == parallel]
    by_id = {s.id: s for s in students}
    by_user = {s.user_id: s for s in students}

    def group(s):
        return s.school_class if parallel else s.par

    groups = sorted({group(s) for s in students}, key=_parallel_key if not parallel else str)
    stat = {g: dict(name=g, students=0, active=0, logins=0, solved=0, graded=0, scores=[], sor=[], soch=[], wrote=0)
            for g in groups}
    for s in students:
        stat[group(s)]['students'] += 1

    # ---- Заходы ----
    weeks = _weeks(now)
    week_idx = {w: i for i, w in enumerate(weeks)}
    uniq_week = [set() for _ in weeks]
    active = set()
    first_event = None
    for user_id, at in LoginEvent.objects.filter(at__gte=SINCE, user_id__in=list(by_user)).values_list('user_id', 'at'):
        s = by_user[user_id]
        active.add(s.id)
        stat[group(s)]['logins'] += 1
        uniq_week[week_idx[_week_start(at)]].add(s.id)
        first_event = at if first_event is None or at < first_event else first_event
    total_logins = sum(g['logins'] for g in stat.values())
    for s in students:        # кто заходил до появления журнала входов — считаем по последнему входу
        if s.id not in active and s.user.last_login and s.user.last_login >= SINCE:
            active.add(s.id)
    for sid in active:
        stat[group(by_id[sid])]['active'] += 1

    # ---- Задачи ----
    subs = Submission.objects.filter(is_graded=True, updated_at__gte=SINCE, student_id__in=list(by_id)) \
        .values('student_id', 'score', 'updated_at')
    solved_week = [0] * len(weeks)
    all_scores = []
    for sub in subs:
        g = stat[group(by_id[sub['student_id']])]
        g['graded'] += 1
        g['scores'].append(sub['score'] or 0)
        all_scores.append(sub['score'] or 0)
        if sub['score'] == 10:
            g['solved'] += 1
            solved_week[week_idx[_week_start(sub['updated_at'])]] += 1

    # ---- СОР/СОЧ ----
    from .models import ExamAnswer
    exams = {e.id: e for e in Exam.objects.filter(kind__in=['SOR', 'SOCH'], start_at__gte=SINCE).prefetch_related('tasks')}
    attempts = [a for a in ExamAttempt.objects.filter(exam_id__in=list(exams), retake__isnull=True, student_id__in=list(by_id))
                if not a.is_active(now)]
    ans = defaultdict(list)
    for a in ExamAnswer.objects.filter(attempt_id__in=[a.id for a in attempts]).only('attempt_id', 'best_score'):
        ans[a.attempt_id].append(a)
    exam_rows = {}
    grades = {'SOR': [], 'SOCH': []}
    quarter = defaultdict(lambda: {'SOR': [], 'SOCH': []})
    for a in attempts:
        e = exams[a.exam_id]
        n = len(a.task_ids) if (e.is_random and a.task_ids) else e.tasks.count()
        gr = attempt_grade(a, ans, n)
        grades[e.kind].append(gr)
        quarter[e.quarter or 0][e.kind].append(gr)
        st = stat[group(by_id[a.student_id])]
        st['sor' if e.kind == 'SOR' else 'soch'].append(gr)
        row = exam_rows.setdefault(e.id, dict(exam=e, grades=[]))
        row['grades'].append(gr)
    all_grades = grades['SOR'] + grades['SOCH']

    class_size = defaultdict(int)
    for s in students:
        class_size[s.school_class] += 1
    exam_list = []
    for e in sorted(exams.values(), key=lambda x: x.start_at, reverse=True):
        if parallel and grade_of(e.school_class) != parallel:
            continue
        g = exam_rows.get(e.id, {}).get('grades', [])
        exam_list.append(dict(id=e.id, title=e.title, kind=e.kind, school_class=e.school_class,
                              date=timezone.localtime(e.start_at), wrote=len(g), size=class_size.get(e.school_class, 0),
                              avg=_avg(g)))
    # у каждой группы — число учеников, писавших хотя бы что-то
    for g in stat.values():
        g['wrote'] = len(g['sor']) + len(g['soch'])

    rows = []
    for g in groups:
        s = stat[g]
        rows.append(dict(name=g, students=s['students'], active=s['active'],
                         active_pct=round(100 * s['active'] / s['students']) if s['students'] else 0,
                         logins=s['logins'], solved=s['solved'], graded=s['graded'], avg_score=_avg(s['scores']),
                         sor=_avg(s['sor']), soch=_avg(s['soch']), sor_n=len(s['sor']), soch_n=len(s['soch'])))

    dist = []
    for label, lo, hi, color in BANDS:
        dist.append(dict(label=label, color=color, count=sum(1 for x in all_grades if lo <= x < hi)))
    quarters = [dict(n=q, sor=_avg(v['SOR']), soch=_avg(v['SOCH']), sor_n=len(v['SOR']), soch_n=len(v['SOCH']))
                for q, v in sorted(quarter.items()) if q]

    return dict(
        parallel=parallel, parallels=pars, since=timezone.localtime(SINCE).date(),
        by_group='class' if parallel else 'parallel',
        first_event=timezone.localtime(first_event).date() if first_event else None,
        kpi=dict(students=len(students), active=len(active),
                 active_pct=round(100 * len(active) / len(students)) if students else 0,
                 logins=total_logins, solved=sum(g['solved'] for g in stat.values()), avg_score=_avg(all_scores),
                 exams=len({a.exam_id for a in attempts}), attempts=len(attempts),
                 sor=_avg(grades['SOR']), soch=_avg(grades['SOCH']), grade=_avg(all_grades)),
        rows=rows, exams=exam_list, dist=dist, quarters=quarters,
        weeks=[w.strftime('%d.%m') for w in weeks],
        week_active=[len(u) for u in uniq_week], week_solved=solved_week,
    )


def _is_admin(user):
    return user.is_active and user.is_superuser


@login_required
@user_passes_test(_is_admin, login_url='/')
def admin_dashboard(request):
    parallel = (request.GET.get('p') or '').strip() or None
    data = build(parallel)
    return render(request, 'admin_dashboard.html', {'d': data})
