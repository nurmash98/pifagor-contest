"""Значки учеников: каталог, подсчёт и выдача.

Значки за задачи — по числу задач с оценкой 10/10 (как «Решено» в профиле).
Значки за СОР/СОЧ — по оценке 10 на завершённых экзаменах и пересдачах.
Выдача — sync_badges(student): создаёт недостающие StudentBadge с датой, когда условие
реально выполнилось (а не когда ученик зашёл в профиль)."""
from django.utils import timezone

from .models import ExamAttempt, StudentBadge, Submission

# kind: 'tasks' (порог = число задач) / 'exam'. Цвета: c1 — рамка, c2 — светлый тон.
CATALOG = [
    dict(key='t10', kind='tasks', need=10, emoji='🌱', c1='#c97b3c', c2='#f0b27a',
         ru='Новичок', kk='Бастаушы',
         ru_d='Решите 10 задач на оценку 10/10.', kk_d='10/10 бағамен 10 есеп шешіңіз.'),
    dict(key='t50', kind='tasks', need=50, emoji='⚔️', c1='#8d99ae', c2='#e3e8ef',
         ru='Боец', kk='Жауынгер',
         ru_d='Решите 50 задач на оценку 10/10.', kk_d='10/10 бағамен 50 есеп шешіңіз.'),
    dict(key='t100', kind='tasks', need=100, emoji='🎖️', c1='#d4a017', c2='#ffe27a',
         ru='Ветеран', kk='Ардагер',
         ru_d='Решите 100 задач на оценку 10/10.', kk_d='10/10 бағамен 100 есеп шешіңіз.'),
    dict(key='t200', kind='tasks', need=200, emoji='💎', c1='#2ea58a', c2='#8ff0d4',
         ru='Элита', kk='Таңдаулы',
         ru_d='Решите 200 задач на оценку 10/10.', kk_d='10/10 бағамен 200 есеп шешіңіз.'),
    dict(key='t500', kind='tasks', need=500, emoji='🏆', c1='#6a5acd', c2='#c3b8ff',
         ru='Чемпион', kk='Жеңімпаз',
         ru_d='Решите 500 задач на оценку 10/10.', kk_d='10/10 бағамен 500 есеп шешіңіз.'),
    dict(key='t1000', kind='tasks', need=1000, emoji='👑', c1='#d23b5a', c2='#ff9fb4',
         ru='Легенда', kk='Аңыз',
         ru_d='Решите 1000 задач на оценку 10/10.', kk_d='10/10 бағамен 1000 есеп шешіңіз.'),

    dict(key='e_first', kind='exam', need=1, emoji='🔟', c1='#d4a017', c2='#ffe27a',
         ru='Первая десятка', kk='Алғашқы он',
         ru_d='Первый раз получите 10/10 на СОР или СОЧ.',
         kk_d='БЖБ немесе ТЖБ-дан алғаш рет 10/10 алыңыз.'),
    dict(key='e_sor', kind='exam', need=1, emoji='🥇', c1='#d4a017', c2='#ffe27a',
         ru='Отличник СОР', kk='БЖБ үздігі',
         ru_d='Получите 10/10 на любом СОР.', kk_d='Кез келген БЖБ-дан 10/10 алыңыз.'),
    dict(key='e_soch', kind='exam', need=1, emoji='🏆', c1='#d23b5a', c2='#ffb3c1',
         ru='Отличник СОЧ', kk='ТЖБ үздігі',
         ru_d='Получите 10/10 на любом СОЧ.', kk_d='Кез келген ТЖБ-дан 10/10 алыңыз.'),
    dict(key='e_streak', kind='exam', need=3, emoji='🔥', c1='#e8741a', c2='#ffcf9f',
         ru='Серия побед', kk='Жеңістер сериясы',
         ru_d='Получите 10/10 на трёх СОР или СОЧ подряд (пропущенный экзамен серию не прерывает).',
         kk_d='Үш БЖБ немесе ТЖБ қатарынан 10/10 алыңыз (өткізіп алған емтихан серияны үзбейді).'),
    dict(key='e_fixed', kind='exam', need=1, emoji='🔁', c1='#2ea58a', c2='#8ff0d4',
         ru='Работа над ошибками', kk='Қатемен жұмыс',
         ru_d='На пересдаче получите оценку выше, чем на самом экзамене.',
         kk_d='Қайта тапсыруда емтиханның өзіндегіден жоғары баға алыңыз.'),
    dict(key='e_five', kind='exam', need=5, emoji='✋', c1='#6a5acd', c2='#c3b8ff',
         ru='Пять десяток', kk='Бес ондық',
         ru_d='Получите 10/10 на пяти разных СОР или СОЧ.',
         kk_d='Бес түрлі БЖБ немесе ТЖБ-дан 10/10 алыңыз.'),
]
BY_KEY = {b['key']: b for b in CATALOG}


def _finished_attempts(student):
    """Завершённые попытки экзаменов и пересдач ученика, по времени окончания."""
    now = timezone.now()
    rows = []
    for a in (ExamAttempt.objects.filter(student=student)
              .select_related('exam', 'retake').prefetch_related('answers')):
        if a.is_active(now):
            continue
        rows.append((a.finished_at or a.window.end_at, a))
    rows.sort(key=lambda r: r[0])
    return rows


def compute(student):
    """({key: когда получен}, {key: (текущее, нужно)}) по данным ученика."""
    earned, progress = {}, {}

    perfect = list(Submission.objects.filter(student=student, is_graded=True, score=10)
                   .order_by('updated_at').values_list('updated_at', flat=True))
    for b in CATALOG:
        if b['kind'] != 'tasks':
            continue
        progress[b['key']] = (len(perfect), b['need'])
        if len(perfect) >= b['need']:
            earned[b['key']] = perfect[b['need'] - 1]

    rows = _finished_attempts(student)
    tens = [(when, a) for when, a in rows if a.grade() >= 10]
    first_exam_grade = {}
    best_by_exam = {}
    for when, a in rows:
        g = a.grade()
        if a.retake_id is None:
            first_exam_grade[a.exam_id] = g
        if g >= 10 and a.exam_id not in best_by_exam:
            best_by_exam[a.exam_id] = when

    if tens:
        earned['e_first'] = tens[0][0]
    for key, kind in (('e_sor', 'SOR'), ('e_soch', 'SOCH')):
        hit = [w for w, a in tens if a.exam.kind == kind]
        if hit:
            earned[key] = hit[0]
    progress['e_first'] = (min(len(tens), 1), 1)
    progress['e_sor'] = (min(len([1 for _, a in tens if a.exam.kind == 'SOR']), 1), 1)
    progress['e_soch'] = (min(len([1 for _, a in tens if a.exam.kind == 'SOCH']), 1), 1)

    # Серия: подряд идущие 10/10 по экзаменам (только основные попытки, по дате экзамена);
    # оценка ниже 10 серию обрывает, пропущенный экзамен — нет.
    main = sorted([(a.exam.start_at, a.finished_at or a.window.end_at, a.grade())
                   for _, a in rows if a.retake_id is None])
    run = best = 0
    streak_at = None
    for _start, when, g in main:
        run = run + 1 if g >= 10 else 0
        best = max(best, run)
        if run == 3 and streak_at is None:
            streak_at = when
    progress['e_streak'] = (min(best, 3), 3)
    if streak_at:
        earned['e_streak'] = streak_at

    for when, a in rows:
        if a.retake_id and a.grade() > first_exam_grade.get(a.exam_id, 0) \
                and a.exam_id in first_exam_grade:
            earned['e_fixed'] = when
            break
    progress['e_fixed'] = (1 if 'e_fixed' in earned else 0, 1)

    five = sorted(best_by_exam.values())
    progress['e_five'] = (min(len(five), 5), 5)
    if len(five) >= 5:
        earned['e_five'] = five[4]
    return earned, progress


def sync_badges(student):
    """Создаёт недостающие значки; возвращает список ключей новых."""
    earned, _ = compute(student)
    have = set(StudentBadge.objects.filter(student=student).values_list('key', flat=True))
    new = [k for k in earned if k not in have]
    StudentBadge.objects.bulk_create(
        [StudentBadge(student=student, key=k, awarded_at=earned[k]) for k in new],
        ignore_conflicts=True)
    return new


def profile_badges(student, lang='ru'):
    """Данные для профиля: два списка значков с состоянием, + непросмотренные новые."""
    sync_badges(student)
    _, progress = compute(student)
    got = {b.key: b for b in StudentBadge.objects.filter(student=student)}
    kk = lang == 'kk'
    groups = {'tasks': [], 'exam': []}
    for b in CATALOG:
        have = got.get(b['key'])
        cur, need = progress.get(b['key'], (0, b['need']))
        groups[b['kind']].append({
            **b, 'title': b['kk'] if kk else b['ru'], 'desc': b['kk_d'] if kk else b['ru_d'],
            'earned': have is not None, 'awarded_at': have.awarded_at if have else None,
            'current': cur, 'total': need, 'percent': int(100 * cur / need) if need else 0,
            'left': max(need - cur, 0),
        })
    unseen = [x for x in groups['tasks'] + groups['exam']
              if x['earned'] and not got[x['key']].seen]
    return groups, unseen


def mark_seen(student):
    StudentBadge.objects.filter(student=student, seen=False).update(seen=True)
