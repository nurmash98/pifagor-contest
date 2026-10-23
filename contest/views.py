from django.conf import settings
import json
import logging
import re
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, Case, When, Value, IntegerField, F, Prefetch
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import LoginEvent, Task, Submission, Attempt, Student, Course, Teacher, Topic, Tag, ClassBonus, normalize_school_class
from .forms import StudentRegistrationForm
from .autotest import run_autotests, MAX_TEST_CASES
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponse
from django.views.decorators.clickjacking import xframe_options_sameorigin

logger = logging.getLogger(__name__)

SUBMISSION_COOLDOWN = timedelta(minutes=5)  # пауза между отправками одной задачи


# Вход: после стольких неверных попыток подряд логин блокируется на LOGIN_LOCKOUT_SECONDS
# (без проверки пароля — чтобы многократные нажатия "Войти" не грузили CPU).
LOGIN_MAX_FAILED = 5
LOGIN_LOCKOUT_SECONDS = 60
# Регистрация: повторные нажатия с тем же логином в течение этого времени не создают
# аккаунт заново, а отправляют на страницу входа.
REGISTER_LOCK_SECONDS = 30

# Баллы, которыми учитель может поощрить/наказать целый класс за атмосферу на уроке —
# без какой-либо строгой методики, просто когда учителю хочется. Обычные значения ±1/±2;
# +7 и -6 — крайние оценки для по-настоящему запомнившегося (в любую сторону) урока,
# ставятся редко (это объясняется в критериях на странице лидерборда, не в самой кнопке).
CLASS_BONUS_PRESETS = [
    (7, '+7 · Прям влюбился в класс на весь урок'),
    (2, '+2 · Имба-урок, класс был на волне'),
    (1, '+1 · Было комфортно работать'),
    (-1, '-1 · Не очень комфортно было'),
    (-2, '-2 · Вайб не тот, полный кринж'),
    (-6, '-6 · Вообще не понравилось, как сидели'),
]
CLASS_BONUS_ALLOWED_VALUES = {points for points, _ in CLASS_BONUS_PRESETS}


def _grade_of(school_class):
    """Параллель ученика — числовая часть класса без буквы: '7А' -> '7', '10Б' -> '10'."""
    match = re.match(r'\d+', (school_class or '').strip())
    return match.group() if match else (school_class or '').strip()


# Общий балл в лидерборде = сумма (коэффициент сложности задачи * оценка учителя за неё).
# Коэффициент зависит от уровня задачи: лёгкая — 1, средняя — 2, сложная — 3.
LEADERBOARD_COEFFICIENT_BY_LEVEL = {'A': 1, 'B': 2, 'C': 3}

LEADERBOARD_COEFFICIENT_CASE = Case(
    When(submissions__task__level='A', then=Value(LEADERBOARD_COEFFICIENT_BY_LEVEL['A'])),
    When(submissions__task__level='B', then=Value(LEADERBOARD_COEFFICIENT_BY_LEVEL['B'])),
    When(submissions__task__level='C', then=Value(LEADERBOARD_COEFFICIENT_BY_LEVEL['C'])),
    default=Value(0),
    output_field=IntegerField(),
)

# score * коэффициент — очки за одну решённую задачу.
LEADERBOARD_SCORE_EXPR = F('submissions__score') * LEADERBOARD_COEFFICIENT_CASE


def _class_leaderboard_rows(school_classes):
    """Рейтинг классов: средний балл (округлённый до целого) по ВСЕМ ученикам класса
    + бонусные баллы, которые классу вручную поставил учитель за атмосферу на уроке.
    school_classes — классы, которые нужно включить в таблицу (одной параллели)."""
    rows = []
    for cls in school_classes:
        students_in_class = Student.objects.filter(school_class=cls).annotate(
            total_score=Sum(LEADERBOARD_SCORE_EXPR, filter=Q(submissions__is_graded=True)),
        )
        scores = [student.total_score or 0 for student in students_in_class]
        avg_score = round(sum(scores) / len(scores)) if scores else 0
        bonus = ClassBonus.objects.filter(school_class=cls).aggregate(total=Sum('points'))['total'] or 0
        rows.append({
            'school_class': cls,
            'student_count': len(scores),
            'avg_score': avg_score,
            'bonus': bonus,
            'total': avg_score + bonus,
        })
    rows.sort(key=lambda row: row['total'], reverse=True)
    return rows


def _tasks_by_level_and_solved(qs=None):
    """Задачи с числом учеников, решивших их на 10/10 (task.solved_count), в порядке:
    уровень A→B→C, внутри уровня — сначала самые решаемые, затем по названию.
    Подсчёт и сортировка — одним SQL-запросом в базе (COUNT + ORDER BY), без
    перебора в Python."""
    qs = Task.objects.all() if qs is None else qs
    return qs.annotate(
        solved_count=Count('submission', filter=Q(submission__is_graded=True, submission__score=10)),
    ).order_by('level', '-solved_count', 'title')


def _minutes_left(delta):
    """Сколько минут ждать (с округлением вверх, минимум 1)."""
    return max(1, -(-int(delta.total_seconds()) // 60))


def _cooldown_remaining(submission):
    """Сколько ещё осталось ждать до повторной отправки этой задачи (или None, если можно отправлять)."""
    if not submission.last_submitted_at:
        return None
    elapsed = timezone.now() - submission.last_submitted_at
    if elapsed >= SUBMISSION_COOLDOWN:
        return None
    return SUBMISSION_COOLDOWN - elapsed


def _autotest_is_stale(submission):
    """Нужно ли (пере)запускать автотесты: ещё не пробовали ни разу, либо ученик
    отправил код заново после последнего прогона (last_submitted_at обновился)."""
    if submission.autotest_checked_at is None:
        return True
    if submission.last_submitted_at and submission.last_submitted_at > submission.autotest_checked_at:
        return True
    return False


def _record_attempt(submission, key_count=None, pointer_count=None):
    """Сохраняет только что отправленный код как новую попытку (история попыток по задаче)."""
    last = submission.attempts.order_by('-number').first()
    return Attempt.objects.create(
        submission=submission,
        number=(last.number + 1) if last else 1,
        code=submission.code or '',
        submitted_at=submission.last_submitted_at or timezone.now(),
        key_count=key_count,
        pointer_count=pointer_count,
    )


def _count_param(request, name):
    """Число из скрытого поля формы (нажатия/движения) или None, если поля нет/оно не число."""
    raw = request.POST.get(name, '')
    return min(int(raw), 10 ** 6) if raw.isdigit() else None


def _refresh_autotests(submission):
    """Прогоняет код ученика через тест-кейсы задачи (contest/autotest.py) и сохраняет
    результат прямо в отправке. Возвращает результат прогона (dict из run_autotests).
    Никогда не бросает исключение наружу — не должно мешать ученику отправить решение,
    даже если сам прогон тестов почему-то упал."""
    try:
        result = run_autotests(submission.task, submission.code or '')
    except Exception:
        logger.exception('Непредвиденная ошибка автотестов решения #%s', submission.pk)
        result = {'available': False}

    submission.autotest_checked_at = timezone.now()
    update_fields = ['autotest_checked_at']
    if result.get('available'):
        submission.autotest_passed = result.get('passed')
        submission.autotest_total = result.get('total')
        submission.autotest_results = result.get('results', [])
        update_fields += ['autotest_passed', 'autotest_total', 'autotest_results']
    submission.save(update_fields=update_fields)
    return result


def autotest_score(passed, total):
    """Балл по автотестам: доля пройденных тестов из 10 (для 5 тестов — по 2 балла за тест:
    5/5 → 10, 4/5 → 8, 3/5 → 6, 2/5 → 4, 1/5 → 2, 0/5 → 0)."""
    if not total:
        return 0
    return int(10 * passed / total + 0.5)


def _submit_attempt(submission, key_count=None, pointer_count=None):
    """Всё, что происходит после сохранения нового кода: попытка в истории, автотесты и —
    если у задачи есть тесты — сразу оценка по ним. Итоговая оценка по задаче остаётся
    лучшей из всех попыток; учитель может потом исправить автоматическую оценку.
    Возвращает текст сообщения для ученика."""
    attempt = _record_attempt(submission, key_count, pointer_count)
    if not submission.task.test_cases:
        return 'Код отправлен! Преподаватель проверит ваше решение и выставит балл.'

    result = _refresh_autotests(submission)
    if not result.get('available'):
        # Тесты не смогли запуститься — оставляем решение на ручную проверку учителем.
        return 'Код отправлен! Преподаватель проверит ваше решение и выставит балл.'

    passed, total = result['passed'], result['total']
    attempt.score = autotest_score(passed, total)
    attempt.auto_graded = True
    attempt.tests_passed, attempt.tests_total = passed, total
    attempt.graded_at = timezone.now()
    attempt.save()

    final_score = submission.recalc_final_score()
    from . import quarters as _quarters
    _quarters.invalidate()
    submission.teacher_comment = ''
    submission.status = 'DONE'
    submission.save()
    return (f'Код проверен автоматически: пройдено тестов {passed} из {total} — '
            f'{attempt.score} баллов. Итоговая оценка по задаче (лучшая из попыток): {final_score}/10.')


def _safe_back_url(request):
    """Достаём ?back=... из запроса и проверяем, что это безопасная локальная ссылка,
    чтобы кнопка 'Назад' вела туда, откуда пользователь пришёл (каталог, курс)."""
    raw_back = request.GET.get('back') or request.POST.get('back')
    if raw_back and url_has_allowed_host_and_scheme(
        raw_back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return raw_back
    return None


def _get_content_lang(request):
    """Текущий выбранный язык условий задач ('ru' или 'kk'), из сессии пользователя."""
    lang = request.session.get('content_lang', 'ru')
    return lang if lang in ('ru', 'kk') else 'ru'


def _apply_task_language(tasks, lang):
    """Проставляет task.display_title/description/input_example/output_example
    с учётом выбранного языка — если казахского перевода нет, используем русский."""
    for task in tasks:
        task.display_title = task.get_display_title(lang)
        task.display_description = task.get_display_description(lang)
        # В старых примерах перенос строки записан текстом "\\n" — показываем как настоящий перенос.
        task.display_input_example = task.get_display_input_example(lang).replace('\\n', '\n')
        task.display_output_example = task.get_display_output_example(lang).replace('\\n', '\n')
    return tasks


def set_content_lang(request, lang):
    """Переключатель языка условий задач (RU/KZ) в шапке сайта — сохраняем в сессии
    и возвращаем пользователя туда, откуда он переключил язык."""
    if lang in ('ru', 'kk'):
        request.session['content_lang'] = lang
    back_url = _safe_back_url(request)
    return redirect(back_url or 'all_tasks')


@login_required
def all_tasks_view(request):
    """Главная страница: каталог всех задач в виде таблицы.
    Ученику показываем его статус по каждой задаче; преподавателю — тот же каталог
    в режиме просмотра (без статусов решения, которых у него просто нет)."""
    student = Student.objects.filter(user=request.user).first()
    lang = _get_content_lang(request)

    level_filter = request.GET.get('level', '')
    tasks = Task.objects.prefetch_related('tags').all()
    if level_filter in ['A', 'B', 'C']:
        tasks = tasks.filter(level=level_filter)

    # Фильтр по тегу — доступен и ученикам, и преподавателям (общий каталог задач).
    tag_id_raw = request.GET.get('tag', '')
    selected_tag_id = None
    if tag_id_raw:
        try:
            selected_tag_id = int(tag_id_raw)
        except (TypeError, ValueError):
            selected_tag_id = None
    if selected_tag_id:
        tasks = tasks.filter(tags__id=selected_tag_id)

    tasks = list(_tasks_by_level_and_solved(tasks))
    _apply_task_language(tasks, lang)

    status_filter = request.GET.get('status', '')
    if status_filter not in ('done', 'unsolved', 'progress') or student is None:
        status_filter = ''

    if student is not None:
        # Привязываем существующие решения ученика к задачам
        user_submissions = {
            sub.task_id: sub
            for sub in Submission.objects.filter(student=student)
        }
        for task in tasks:
            task.user_sub = user_submissions.get(task.id)
        if status_filter:
            def _keep(task):
                sub = task.user_sub
                done = bool(sub and sub.status == 'DONE' and sub.score == 10)  # решено = ровно 10/10
                if status_filter == 'done':
                    return done
                if status_filter == 'unsolved':
                    return not done
                return bool(sub) and not done  # progress: начата, но не 10/10 (в т.ч. 0/10, 9/10)
            tasks = [t for t in tasks if _keep(t)]
    else:
        for task in tasks:
            task.user_sub = None

    def _url(level=None, tag=None, status=None):
        params = {}
        if (level if level is not None else level_filter) in ('A', 'B', 'C'):
            params['level'] = level if level is not None else level_filter
        tag_value = tag if tag is not None else selected_tag_id
        if tag_value:
            params['tag'] = tag_value
        status_value = status if status is not None else status_filter
        if status_value:
            params['status'] = status_value
        base = reverse('all_tasks')
        return f'{base}?{urlencode(params)}' if params else base

    all_tags = list(Tag.objects.all())
    context = {
        'tasks': tasks,
        'current_level': level_filter,
        'current_tag': selected_tag_id,
        'current_status': status_filter,
        'all_tags': all_tags,
        'readonly': student is None,
        'level_urls': {(k or 'all'): _url(level=k) for k in ('', 'A', 'B', 'C')},
        'status_urls': {(k or 'all'): _url(status=k) for k in ('', 'done', 'unsolved', 'progress')},
        'all_tags_url': _url(tag=0),
        'tag_urls': [(tag, _url(tag=tag.id)) for tag in all_tags],
    }
    return render(request, 'all_tasks.html', context)


def _input_kind(task):
    """'lines' — ввод на нескольких строках, 'one_line' — несколько значений в одной строке,
    '' — не определить (одно значение или пример пуст)."""
    raw = (task.input_example or '').replace('\\n', '\n').strip()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) > 1:
        return 'lines'
    if len(lines) == 1 and len(lines[0].split()) > 1:
        return 'one_line'
    return ''


def _open_input_help(failed):
    err = (failed or {}).get('error') or ''
    return err.startswith(('ValueError', 'EOFError'))


def _first_failed(results, task):
    """Первый упавший тест (dict с полем number) или None. Для старых отправок без
    сохранённого ввода берём ввод из тест-кейса задачи."""
    for i, r in enumerate(results or [], 1):
        if not r.get('ok'):
            failed = dict(r, number=i)
            if 'input' not in failed:
                cases = task.test_cases or []
                if i <= len(cases):
                    failed['input'] = str(cases[i - 1].get('input', ''))
            return failed
    return None


@login_required
def task_detail(request, task_id):
    """Детальная страница задачи и ручная отправка на проверку преподавателю.
    Преподаватель тоже может открыть любую задачу — но только для просмотра,
    без формы отправки решения (у него нет своих посылок)."""
    task = get_object_or_404(Task, id=task_id)
    student = Student.objects.filter(user=request.user).first()
    back_url = _safe_back_url(request)
    lang = _get_content_lang(request)
    _apply_task_language([task], lang)

    if student is None:
        # Преподаватель/админ — только просмотр условия задачи
        teacher_code, teacher_result, teacher_failed = '', None, None
        if request.method == 'POST':
            # Преподаватель решает задачу «для себя»: прогон тестов без сохранения
            # (нет ни посылки, ни баллов, ни значков, ни места в лидерборде).
            teacher_code = request.POST.get('code', '')
            run = run_autotests(task, teacher_code) if teacher_code.strip() else {'available': False, 'empty': True}
            if run.get('empty'):
                messages.info(request, 'Вставьте код решения — поле пустое.')
            elif run.get('available'):
                from types import SimpleNamespace
                teacher_result = SimpleNamespace(
                    autotest_checked_at=timezone.now(), autotest_total=run['total'],
                    autotest_passed=run['passed'], autotest_results=run['results'])
                teacher_failed = _first_failed(run['results'], task)
            else:
                messages.info(request, 'У этой задачи нет автотестов, проверить код нечем.')
        context = {
            'task': task,
            'submission': None,
            'cooldown_minutes': None,
            'back_url': back_url or reverse('all_tasks'),
            'readonly': True,
            'teacher_code': teacher_code,
            'teacher_result': teacher_result,
            'teacher_failed': teacher_failed,
            'input_kind': _input_kind(task),
            'open_help': _open_input_help(teacher_failed),
        }
        return render(request, 'task_detail.html', context)

    task_url = reverse('task_detail', args=[task.id])
    task_url_with_back = f'{task_url}?back={quote(back_url, safe="")}' if back_url else task_url

    submission = Submission.objects.filter(student=student, task=task).first()

    # Если задача еще не взята в работу
    if not submission:
        submission = Submission.objects.create(student=student, task=task, status='IN_PROGRESS')

    cooldown = _cooldown_remaining(submission)

    # ОБРАБОТКА ОТПРАВКИ КОДА — не чаще одного раза в 5 минут (SUBMISSION_COOLDOWN)
    if request.method == 'POST':
        if cooldown:
            messages.warning(request, f'Эту задачу можно отправлять раз в 5 минут. Попробуйте снова через {_minutes_left(cooldown)} мин.')
            return redirect(task_url_with_back)

        code = request.POST.get('code', '')
        if not code.strip():
            messages.warning(request, 'Вставьте код решения — поле пустое.')
            return redirect(task_url_with_back)
        submission.code = code
        submission.status = 'TESTING'  # Отправляем на проверку преподавателю
        submission.last_submitted_at = timezone.now()
        submission.save()
        messages.success(request, _submit_attempt(
            submission, _count_param(request, 'key_count'), _count_param(request, 'pointer_count')))
        from . import banners as banner_module
        from . import coins as coin_module
        got = coin_module.sync(student)
        if got:
            messages.success(request, f'🪙 +{got} {"тиын" if _get_content_lang(request) == "kk" else "монет"}')
        if banner_module.sync(student):
            messages.success(request, 'Тәуелсіз Қазақстанның тарихын білесіз! Сізге «Қазақстан туы» баннері берілді 🇰🇿'
                             if _get_content_lang(request) == 'kk' else
                             'Вы знаете историю Казахстана! Вам вручён баннер «Флаг Казахстана» под вашим именем 🇰🇿')
        return redirect(task_url_with_back)

    first_failed = _first_failed(submission.autotest_results, task)

    context = {
        'task': task,
        'submission': submission,
        'first_failed': first_failed,
        'input_kind': _input_kind(task),
        'open_help': _open_input_help(first_failed),
        'attempts': submission.attempts.all(),
        'cooldown_minutes': _minutes_left(cooldown) if cooldown else None,
        'back_url': back_url or reverse('all_tasks'),
        'readonly': False,
    }
    return render(request, 'task_detail.html', context)


@login_required
def profile_view(request):
    """Профиль ученика: общий балл (как в лидерборде — коэффициент сложности * оценка за
    КАЖДУЮ проверенную задачу) и количество РЕШЁННЫХ задач — тех, где оценка 10/10
    (а не любых проверенных). Средний балл здесь не показывается."""
    student = get_object_or_404(Student, user=request.user)

    # Вся история проверенных задач (для списка "История решений" — с любой оценкой).
    solved_submissions = Submission.objects.filter(
        student=student, is_graded=True
    ).select_related('task').order_by('-updated_at')

    # "Решено" — только задачи, где оценка максимальная (10/10).
    solved_count = solved_submissions.filter(score=10).count()
    total_score = sum(
        sub.score * LEADERBOARD_COEFFICIENT_BY_LEVEL.get(sub.task.level, 0)
        for sub in solved_submissions
    )

    from . import badges as badge_module
    groups, new_badges = badge_module.profile_badges(student, _get_content_lang(request))
    badge_groups = [
        ('Значки за задачи', {'items': groups['tasks'], 'earned_count': sum(1 for b in groups['tasks'] if b['earned'])}, '🏅'),
        ('Значки за СОР/СОЧ', {'items': groups['exam'], 'earned_count': sum(1 for b in groups['exam'] if b['earned'])}, '⭐'),
    ]
    badge_module.mark_seen(student)

    from . import avatars as avatar_module
    from django.templatetags.static import static
    lang = _get_content_lang(request)
    avatar_options = [{'key': '', 'img': '', 'title': 'Әдепкі' if lang == 'kk' else 'По умолчанию',
                       'on': not student.avatar}]
    for a in avatar_module.available_for(student):
        avatar_options.append({'key': a['key'], 'img': static(a['file']), 'title': a['kk'] if lang == 'kk' else a['ru'],
                               'on': student.avatar == a['key']})

    from . import banners as banner_module
    from . import coins as coin_module
    banner_module.sync(student)
    coin_module.sync(student)
    owned_banners = banner_module.owned(student)
    banner_options = [{'key': '', 'title': 'Жоқ' if lang == 'kk' else 'Без баннера', 'on': not student.name_banner, 'owned': True}]
    for key in owned_banners:
        b = banner_module.CATALOG[key]
        banner_options.append({'key': key, 'title': b['kk'] if lang == 'kk' else b['ru'], 'owned': True,
                               'on': student.name_banner == key})

    context = {
        'banner_options': banner_options,
        'republic_progress': min(banner_module.holiday_progress(student), banner_module.REPUBLIC_NEED),
        'republic_need': banner_module.REPUBLIC_NEED,
        'avatar_options': avatar_options,
        'student': student,
        'solved_submissions': solved_submissions,
        'solved_count': solved_count,
        'total_score': total_score,
        'groups': badge_groups,
        'new_badges': new_badges,
    }
    return render(request, 'profile.html', context)


@login_required
def profile_avatar(request):
    """Ученик выбирает аватар (пусто — аватар по умолчанию 🎓)."""
    from . import avatars as avatar_module
    student = get_object_or_404(Student, user=request.user)
    if request.method == 'POST':
        key = request.POST.get('avatar', '')
        allowed = {a['key'] for a in avatar_module.available_for(student)}
        if key == '' or key in allowed:
            student.avatar = key
            student.save(update_fields=['avatar'])
    return redirect('shop' if request.POST.get('next') == 'shop' else 'profile')


@login_required
def profile_banner(request):
    """Ученик ставит под имя один из полученных баннеров (или убирает баннер)."""
    from . import banners as banner_module
    student = get_object_or_404(Student, user=request.user)
    if request.method == 'POST':
        key = request.POST.get('banner', '')
        if key == '' or key in banner_module.owned(student):
            student.name_banner = key
            student.save(update_fields=['name_banner'])
    return redirect('shop' if request.POST.get('next') == 'shop' else 'profile')


@login_required
def shop(request):
    """Магазин: баннеры под именем (3 бесплатных, подарок, 7 за монеты), аватары, история монет."""
    from django.templatetags.static import static
    from . import quarters
    from . import avatars as avatar_module
    from . import banners as banner_module
    from . import coins as coin_module
    from .models import CoinTransaction
    student = Student.objects.filter(user=request.user).first()
    lang = _get_content_lang(request)
    if student is not None:
        banner_module.sync(student)
        coin_module.sync(student)
    mine = banner_module.owned(student) if student else banner_module.FREE
    bal = coin_module.balance(student)
    banners = []
    for key, b in banner_module.CATALOG.items():
        banners.append({'key': key, 'kind': b['kind'], 'price': b.get('price', 0), 'title': b['kk'] if lang == 'kk' else b['ru'],
                        'custom_style': banner_module.custom_style(student.custom_banner if student else {}) if key == 'custom' else '',
                        'custom': banner_module.clean_custom(student.custom_banner if student else {}) if key == 'custom' else None,
                        'owned': key in mine, 'on': bool(student and student.name_banner == key),
                        'short': max(b.get('price', 0) - bal, 0)})
    avatar_list = [{'key': '', 'img': '', 'kind': 'free', 'title': 'Әдепкі' if lang == 'kk' else 'По умолчанию',
                    'on': bool(student and not student.avatar)}]
    for a in avatar_module.CATALOG:
        avatar_list.append({'key': a['key'], 'img': static(a['file']) if a['file'] else '', 'kind': a['kind'],
                            'title': a['kk'] if lang == 'kk' else a['ru'], 'on': bool(student and student.avatar == a['key'])})
    history = list(CoinTransaction.objects.filter(student=student)[:15]) if student else []
    return render(request, 'shop.html', {
        'student': student, 'balance': bal, 'banners': banners, 'avatars': avatar_list, 'history': history,
        'rules': coin_module, 'awards': quarters.AWARD_COINS, 'award_rest': quarters.AWARD_COINS_REST, 'republic_progress': min(banner_module.holiday_progress(student), banner_module.REPUBLIC_NEED),
        'republic_need': banner_module.REPUBLIC_NEED,
    })


@login_required
def shop_buy(request):
    """Покупка баннера за монеты; купленный баннер сразу ставится под имя."""
    from . import banners as banner_module
    from . import coins as coin_module
    student = get_object_or_404(Student, user=request.user)
    kk = _get_content_lang(request) == 'kk'
    if request.method == 'POST':
        key = request.POST.get('banner', '')
        item = banner_module.CATALOG.get(key)
        if item is None or item['kind'] != 'paid':
            messages.error(request, 'Бұл баннерді сатып алуға болмайды.' if kk else 'Этот баннер нельзя купить.')
        elif key in banner_module.owned(student):
            messages.info(request, 'Бұл баннер сізде бар.' if kk else 'Этот баннер у вас уже есть.')
        elif coin_module.spend(student, f'buy:banner:{key}', item['price'], item['ru']):
            banner_module.give(student, key)
            student.name_banner = key
            student.save(update_fields=['name_banner'])
            if key == 'custom':
                student.custom_banner = banner_module.clean_custom(student.custom_banner)
                student.save(update_fields=['custom_banner'])
                messages.success(request, 'Өз баннеріңізді жасаңыз!' if kk else 'Теперь настройте свой баннер!')
                return redirect('shop_custom')
            messages.success(request, (f'«{item["kk"]}» баннері сатып алынды!' if kk else f'Баннер «{item["ru"]}» куплен и стоит под вашим именем!'))
        else:
            messages.warning(request, 'Тиын жетпейді.' if kk else 'Не хватает монет.')
    return redirect('shop')


@login_required
def shop_custom(request):
    """Редактор «своего баннера»: два цвета фона, цвет текста, значок и эффект."""
    from . import banners as banner_module
    student = get_object_or_404(Student, user=request.user)
    if 'custom' not in banner_module.owned(student):
        return redirect('shop')
    if request.method == 'POST':
        student.custom_banner = banner_module.clean_custom({
            'c1': request.POST.get('c1'), 'c2': request.POST.get('c2'), 'fg': request.POST.get('fg'),
            'icon': request.POST.get('icon', ''), 'fx': request.POST.get('fx')})
        student.name_banner = 'custom'
        student.save(update_fields=['custom_banner', 'name_banner'])
        messages.success(request, 'Баннер сақталды!' if _get_content_lang(request) == 'kk' else 'Баннер сохранён!')
        return redirect('shop')
    return render(request, 'shop_custom.html', {
        'student': student, 'c': banner_module.clean_custom(student.custom_banner),
        'icons': banner_module.CUSTOM_ICONS, 'fxs': banner_module.CUSTOM_FX,
    })


@login_required
def hall_of_fame(request):
    """Зал славы: зафиксированные топ-10 учеников и рейтинг классов каждой параллели по четвертям,
    плюс текущие итоги учебного года."""
    from . import quarters
    from .models import HallEntry
    quarters.freeze_due()
    student = Student.objects.filter(user=request.user).first()
    frozen = set(HallEntry.objects.filter(year=quarters.YEAR).values_list('quarter', flat=True))
    tabs = [{'key': str(q['n']), 'n': q['n'], 'label': q['label'], 'ready': q['n'] in frozen} for q in quarters.QUARTERS]
    tabs.append({'key': 'year', 'n': 0, 'label': quarters.YEAR_WINDOW['label'], 'ready': True})
    sel = request.GET.get('q', '')
    if sel not in [t['key'] for t in tabs if t['ready']]:
        ready = [t['key'] for t in tabs if t['ready'] and t['key'] != 'year']
        sel = ready[-1] if ready else 'year'
    all_classes = sorted({c for c in Student.objects.values_list('school_class', flat=True) if c})
    if student is not None:
        # ученик видит только свою параллель
        grades = [quarters.grade_of(student.school_class)]
    else:
        managed = _managed_classes(request.user)      # учитель — параллели своих классов, админ — все
        source = all_classes if managed is None else managed
        grades = sorted({quarters.grade_of(c) for c in source}, key=lambda g: (len(g), g))
    grade = request.GET.get('g') or (grades[0] if grades else '')
    if grade not in grades:
        grade = grades[0] if grades else ''
    if sel == 'year':
        studs = [s for s in Student.objects.all() if quarters.grade_of(s.school_class) == grade]
        top = quarters.student_ranking(studs, None, quarters.YEAR_WINDOW['end'], quarter_n=0)[:quarters.TOP]
        rows = [{'place': i, 'st': s, 'name': s.full_name, 'school_class': s.school_class,
                 'score': s.calc_total_score, 'solved': s.calc_solved_count} for i, s in enumerate(top, 1)]
        cls_rows = [{'place': i, 'name': r['school_class'], 'score': r['total'], 'solved': r['student_count']}
                    for i, r in enumerate(quarters.class_rows([c for c in all_classes if quarters.grade_of(c) == grade],
                                                              None, quarters.YEAR_WINDOW['end']), 1)]
        frozen_q = None
    else:
        qs = HallEntry.objects.filter(year=quarters.YEAR, quarter=int(sel), grade=grade).select_related('student')
        rows = [{'place': e.place, 'st': e.student, 'name': e.name, 'school_class': e.school_class, 'score': e.score,
                 'solved': e.solved} for e in qs if e.kind == 'student']
        cls_rows = [{'place': e.place, 'name': e.name, 'score': e.score, 'solved': e.solved} for e in qs if e.kind == 'class'][:quarters.TOP]
        frozen_q = quarters.BY_N[int(sel)]
    from .badge_art import solved_counts
    counts = solved_counts([r['st'].id for r in rows if r['st']])
    for r in rows:
        r['all_solved'] = counts.get(r['st'].id, 0) if r['st'] else 0
    return render(request, 'hall_of_fame.html', {
        'tabs': tabs, 'sel': sel, 'grades': grades, 'grade': grade, 'podium': rows[:3], 'rest': rows[3:],
        'class_rows': cls_rows, 'frozen_q': frozen_q, 'awards': quarters.AWARD_COINS, 'award_rest': quarters.AWARD_COINS_REST,
    })


@login_required
def republic_day(request):
    """Страница подарка ко Дню Республики: 5 задач по истории Казахстана → баннер «Флаг»."""
    from . import banners as banner_module
    student = Student.objects.filter(user=request.user).first()
    lang = _get_content_lang(request)
    tasks = list(Task.objects.filter(tags__name=banner_module.REPUBLIC_TAG).order_by('level', 'id'))
    _apply_task_language(tasks, lang)
    new = banner_module.sync(student) if student else []
    if student is not None:
        best = {s.task_id: s for s in Submission.objects.filter(student=student, task__in=tasks)}
        for t in tasks:
            t.user_sub = best.get(t.id)
    done = banner_module.holiday_progress(student) if student else 0
    need = banner_module.REPUBLIC_NEED
    return render(request, 'republic_day.html', {
        'tasks': tasks, 'student': student, 'done': min(done, need), 'need': need,
        'percent': int(100 * min(done, need) / need), 'has_banner': bool(student and 'kz_flag' in banner_module.owned(student)),
        'just_got': bool(new),
    })


@login_required
def leaderboard(request):
    """Лидерборд. Ученик видит топ-10 своей параллели (например, все 7-е классы вместе,
    независимо от буквы). Учитель видит ВСЕХ учеников своих классов (без ограничения топ-10),
    с фильтром по конкретному классу (по списку классов в его профиле).
    Админ (суперпользователь) без своего класса видит топ-10 по всей школе."""
    student = Student.objects.filter(user=request.user).first()
    my_grade = None
    scope_label = None
    teacher_classes = None
    selected_class = ''
    limit = 10

    if student is not None:
        my_grade = _grade_of(student.school_class)
        scope_label = f'{my_grade} классы'
    elif not request.user.is_superuser:
        teacher = Teacher.objects.filter(user=request.user).first()
        if teacher is not None:
            teacher_classes = teacher.class_list()
            requested_class = normalize_school_class(request.GET.get('class', ''))
            selected_class = requested_class if requested_class in teacher_classes else ''
            scope_label = selected_class or ', '.join(teacher_classes)
            limit = None  # Учитель видит полный список своих классов, без топ-10

    # Рейтинг по четвертям: считаются только баллы за задачи, лучший балл по которым получен в
    # текущей четверти (contest/quarters.py). Закончившиеся четверти фиксируются в «Зале славы».
    from . import quarters
    quarters.freeze_due()
    quarter = quarters.current()
    window = quarter or quarters.YEAR_WINDOW
    students_query = list(Student.objects.all())

    if my_grade is not None:
        # Фильтруем в Python, т.к. класс — свободный текст ("7А", "10Б"),
        # а нужна именно числовая параллель без буквы.
        students_query = [s for s in students_query if _grade_of(s.school_class) == my_grade]
    elif teacher_classes is not None:
        if selected_class:
            # Учитель выбрал конкретный класс из фильтра
            students_query = [s for s in students_query if s.school_class == selected_class]
        else:
            # Без фильтра — все ученики всех классов учителя (точное совпадение, например "10А", "11Б")
            students_query = [s for s in students_query if s.school_class in teacher_classes]

    students = quarters.student_ranking(list(students_query), window['start'], window['end'], quarter_n=window['n'])
    if limit is not None:
        students = students[:limit]

    # ---- Рейтинг классов этой параллели (или параллелей, если у учителя классы из
    # разных параллелей / у админа нет выбранного фильтра) ----
    all_school_classes = list(
        Student.objects.order_by('school_class').values_list('school_class', flat=True).distinct()
    )

    if my_grade is not None:
        relevant_grades = [my_grade]
    elif teacher_classes is not None:
        if selected_class:
            relevant_grades = [_grade_of(selected_class)]
        else:
            relevant_grades = sorted({_grade_of(c) for c in teacher_classes})
    else:
        # Суперпользователь без выбранного класса — показываем рейтинг по каждой параллели школы.
        relevant_grades = sorted({_grade_of(c) for c in all_school_classes})

    class_leaderboards = []
    for grade in relevant_grades:
        classes_in_grade = sorted({c for c in all_school_classes if _grade_of(c) == grade})
        if classes_in_grade:
            class_leaderboards.append({'grade': grade, 'rows': quarters.class_rows(classes_in_grade, window['start'], window['end'])})

    # Классы, которым текущий пользователь (учитель/админ) может ставить бонусные баллы.
    can_add_class_bonus = request.user.is_staff
    bonus_classes = teacher_classes if teacher_classes is not None else (
        all_school_classes if request.user.is_superuser else []
    )

    # Объявление об итогах прошлой четверти — первые 10 дней после её конца
    from .models import HallEntry
    last_frozen, winner = None, None
    for q in quarters.QUARTERS:
        if q['end'] < timezone.now() and (timezone.now() - q['end']).days < 10:
            last_frozen = q
    if last_frozen and my_grade is not None:
        winner = HallEntry.objects.filter(year=quarters.YEAR, quarter=last_frozen['n'], kind='student',
                                          grade=my_grade, place=1).first()

    # Учитель: кто убран из рейтинга этой четверти (только его классы)
    excluded = []
    if request.user.is_staff:
        from .models import QuarterExclusion
        ex = QuarterExclusion.objects.filter(year=quarters.YEAR, quarter=window['n']).select_related('student')
        mc = _managed_classes(request.user)
        excluded = [e for e in ex if mc is None or e.student.school_class in mc]

    return render(request, 'leaderboard.html', {
        'excluded': excluded,
        'quarter': quarter, 'quarter_days_left': quarters.days_left(quarter) if quarter else None,
        'last_frozen': last_frozen, 'winner': winner,
        'students': students,
        'my_grade': my_grade,
        'scope_label': scope_label,
        'teacher_classes': teacher_classes,
        'selected_class': selected_class,
        'is_full_list': limit is None,
        'class_leaderboards': class_leaderboards,
        'can_add_class_bonus': can_add_class_bonus,
        'bonus_classes': bonus_classes,
        'class_bonus_presets': CLASS_BONUS_PRESETS,
    })


@staff_member_required
def leaderboard_exclude(request, student_id):
    """Учитель убирает ученика своего класса из рейтинга текущей четверти (или возвращает)."""
    from . import quarters
    from .models import QuarterExclusion
    student = _managed_student_or_none(request, student_id)
    q = quarters.current()
    if request.method == 'POST' and student is not None and q is not None:
        if request.POST.get('action') == 'return':
            QuarterExclusion.objects.filter(student=student, year=quarters.YEAR, quarter=q['n']).delete()
            messages.success(request, f'{student.full_name} снова в рейтинге {q["n"]} четверти.')
        else:
            QuarterExclusion.objects.get_or_create(student=student, year=quarters.YEAR, quarter=q['n'],
                                                   defaults={'reason': request.POST.get('reason', '')[:200],
                                                             'created_by': _teacher_or_none(request)})
            messages.success(request, f'{student.full_name} убран(а) из рейтинга {q["n"]} четверти. Места ниже сдвинулись вверх.')
    elif student is None:
        messages.error(request, 'Это ученик не вашего класса.')
    return redirect(_safe_back_url(request) or 'leaderboard')


@staff_member_required
def add_class_bonus(request):
    """Учитель (или админ) ставит целому классу бонусные/штрафные баллы за атмосферу
    на уроке — вручную, кнопкой на странице лидерборда. Никакой методики: баллы
    ставятся просто когда учителю хочется. Учитель может ставить баллы только
    своим классам; админ (суперпользователь) — любому классу школы."""
    if request.method != 'POST':
        return redirect('leaderboard')

    school_class = normalize_school_class(request.POST.get('school_class', ''))
    comment = request.POST.get('comment', '').strip()
    try:
        points = int(request.POST.get('points', ''))
    except (TypeError, ValueError):
        points = None

    teacher = _teacher_or_none(request)
    allowed_classes = teacher.class_list() if teacher is not None else None

    back_url = _safe_back_url(request)

    if points not in CLASS_BONUS_ALLOWED_VALUES:
        messages.error(request, 'Некорректное количество баллов.')
    elif not school_class or (allowed_classes is not None and school_class not in allowed_classes):
        messages.error(request, 'Вы можете ставить баллы только своим классам.')
    else:
        ClassBonus.objects.create(
            school_class=school_class, points=points, given_by=teacher, comment=comment,
        )
        sign = '+' if points > 0 else ''
        messages.success(request, f'Классу {school_class} начислено {sign}{points} балл(ов).')

    return redirect(back_url or 'leaderboard')


def register(request):
    if request.user.is_authenticated:
        return redirect('all_tasks')
    if not settings.REGISTRATION_OPEN:
        messages.info(request, 'Регистрация временно закрыта. Обратитесь к учителю.')
        return redirect('login')

    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            full_name = form.cleaned_data['full_name']
            school_class = form.cleaned_data['school_class']

            # Защита от многократного нажатия "Зарегистрироваться": первый запрос по этому
            # логину берёт "замок" на REGISTER_LOCK_SECONDS. Повторные клики в это время
            # не создают аккаунт заново и не хешируют пароль (это самое тяжёлое по CPU),
            # а сразу отправляют на страницу входа — аккаунт уже создан (или создаётся
            # прямо сейчас первым запросом).
            lock_key = 'register_lock:' + username.strip().lower()
            if not cache.add(lock_key, 1, REGISTER_LOCK_SECONDS):
                messages.info(request, 'Аккаунт уже создан. Войдите со своим логином и паролем.')
                return redirect(f"{reverse('login')}?username={quote(username)}")

            if User.objects.filter(username=username).exists():
                cache.delete(lock_key)  # логин занят кем-то другим — это не повторный клик
                form.add_error('username', 'Этот логин уже занят. Придумайте другой.')
            else:
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(username=username, password=password)
                        Student.objects.create(
                            user=user,
                            full_name=full_name,
                            school_class=school_class
                        )
                except IntegrityError:
                    # Два запроса проскочили одновременно — аккаунт уже создал другой из них.
                    messages.info(request, 'Аккаунт уже создан. Войдите со своим логином и паролем.')
                    return redirect(f"{reverse('login')}?username={quote(username)}")
                except Exception:
                    cache.delete(lock_key)  # аккаунт не создан — не мешаем повторить попытку
                    raise
                auth_login(request, user)
                return redirect('all_tasks')
    else:
        form = StudentRegistrationForm()

    return render(request, 'register.html', {'form': form})


def _login_fail_key(username):
    return 'login_fail:' + (username or '').strip().lower()


def _log_login(request, user):
    """Запоминаем устройство и адрес входа (для списка подозрительных у учителя)."""
    try:
        LoginEvent.objects.create(
            user=user, ip=request.META.get('REMOTE_ADDR') or None,
            user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:300])
    except Exception:
        logger.exception('Не удалось записать вход пользователя %s', user.pk)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('teacher_dashboard' if request.user.is_staff else 'all_tasks')

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        context = {'username': username}

        # Защита от "долбёжки" кнопки Войти: после LOGIN_MAX_FAILED неверных попыток
        # подряд для этого логина — LOGIN_LOCKOUT_SECONDS просто отказываем, даже НЕ
        # проверяя пароль. Проверка пароля (хеширование) — самая дорогая по CPU операция
        # при входе, поэтому пачка повторных попыток больше не нагружает сервер.
        # Считаем по логину, а не по IP: у всей школы обычно один внешний IP,
        # и блокировка по IP заблокировала бы сразу всех учеников.
        fail_key = _login_fail_key(username)
        if cache.get(fail_key, 0) >= LOGIN_MAX_FAILED:
            context['error'] = 'Слишком много неудачных попыток. Подождите минуту и попробуйте снова.'
            return render(request, 'login.html', context, status=429)

        from .moderation_views import release_expired_blocks
        release_expired_blocks()   # срок временной блокировки вышел — аккаунт снова активен
        user = authenticate(request, username=username, password=password) if username and password else None

        if user is not None:
            cache.delete(fail_key)
            auth_login(request, user)
            _log_login(request, user)
            next_url = request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect('teacher_dashboard' if user.is_staff else 'all_tasks')

        cache.set(fail_key, cache.get(fail_key, 0) + 1, LOGIN_LOCKOUT_SECONDS)
        blocked = User.objects.filter(username=username, is_active=False).first() if username and password else None
        if blocked is not None and blocked.check_password(password):
            until = Student.objects.filter(user=blocked).values_list('blocked_until', flat=True).first()
            context['error'] = ('Аккаунт заблокирован до ' + timezone.localtime(until).strftime('%d.%m %H:%M') + '. Обратитесь к учителю.'
                                if until else 'Аккаунт заблокирован. Обратитесь к учителю.')
            return render(request, 'login.html', context)
        context['error'] = 'Неверный логин или пароль'
        return render(request, 'login.html', context)

    # ?username= — подставляем логин (например, после повторного нажатия на регистрации).
    return render(request, 'login.html', {'username': (request.GET.get('username') or '')[:150]})


def logout_view(request):
    auth_logout(request)
    return redirect('login')


def _course_open_for(user, course):
    """Ученик видит курс, если он включён и открыт его классу. Учителя/админ — всегда."""
    if user.is_staff:
        return True
    if not course.is_visible:
        return False
    student = Student.objects.filter(user=user).first()
    return student is not None and course.visible_to_class(student.school_class)


def _class_choices(user):
    """Классы, которые учитель может отметить: его классы; админу — все существующие."""
    classes = _managed_classes(user)
    if classes is None:
        classes = sorted({c for c in Student.objects.values_list('school_class', flat=True) if c})
    return classes


def _class_form_context(user, course=None):
    chosen = set(course.visible_class_list()) if course else set()
    choices = _class_choices(user)
    # Классы курса, которых нет в списке учителя (выбрал админ), не теряем.
    extra = sorted(chosen - set(choices))
    return {'class_choices': [(c, c in chosen) for c in list(choices) + extra]}


def _read_visible_classes(request):
    allowed = set(_class_choices(request.user))
    return [c for c in request.POST.getlist('visible_classes') if normalize_school_class(c) in allowed]


@login_required
def courses_view(request):
    """Каталог курсов, видимых ученикам (и открытый учителям/админу для проверки)."""
    courses = Course.objects.select_related('created_by').prefetch_related('topics__tasks')
    if not request.user.is_staff:
        courses = [c for c in courses.filter(is_visible=True) if _course_open_for(request.user, c)]
    return render(request, 'courses.html', {'courses': courses})


@login_required
def course_detail(request, course_id):
    """Темы и задачи внутри одного курса — со статусом решения для ученика."""
    course = get_object_or_404(Course.objects.select_related('created_by'), id=course_id)
    if not _course_open_for(request.user, course):
        messages.warning(request, 'Этот курс пока недоступен.')
        return redirect('courses')

    # theory_html (до 2 МБ на тему) на странице курса не нужен — только признак, что он есть.
    topics = course.topics.prefetch_related(
        Prefetch('tasks', queryset=_tasks_by_level_and_solved(Task.objects.prefetch_related('tags')))
    ).defer('theory_html')
    lang = _get_content_lang(request)
    for topic in topics:
        _apply_task_language(topic.tasks.all(), lang)

    if not request.user.is_staff:
        student = Student.objects.filter(user=request.user).first()
        if student:
            user_submissions = {
                sub.task_id: sub for sub in Submission.objects.filter(student=student)
            }
            for topic in topics:
                for task in topic.tasks.all():
                    task.user_sub = user_submissions.get(task.id)

    return render(request, 'course_detail.html', {'course': course, 'topics': topics})


def _teacher_or_none(request):
    return None if request.user.is_superuser else Teacher.objects.filter(user=request.user).first()


def _get_owned_course_or_none(request, course_id):
    """Курс, если текущий пользователь — админ или один из назначенных ему учителей."""
    course = get_object_or_404(Course, id=course_id)
    teacher = _teacher_or_none(request)
    if teacher is not None and not course.teachers.filter(pk=teacher.pk).exists():
        return None
    return course


@staff_member_required
def course_create(request):
    """Учитель (или админ) создаёт новый курс прямо в приложении, без Django admin."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Укажите название курса.')
        else:
            teacher = _teacher_or_none(request)
            course = Course.objects.create(
                name=name,
                description=request.POST.get('description', '').strip(),
                is_visible=request.POST.get('is_visible') == 'on',
                created_by=teacher,
            )
            course.set_visible_classes(_read_visible_classes(request))
            course.save(update_fields=['visible_classes'])
            if teacher is not None:
                teacher.courses.add(course)
            messages.success(request, f'Курс «{course.name}» создан. Теперь добавьте темы.')
            return redirect('course_manage', course_id=course.id)

    return render(request, 'course_form.html', _class_form_context(request.user))


@staff_member_required
def course_manage(request, course_id):
    """Страница курса для учителя: темы, теория, задачи — без захода в админку."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    topics = course.topics.prefetch_related('tasks').defer('theory_html')
    context = {'course': course, 'topics': topics}
    context.update(_class_form_context(request.user, course))
    return render(request, 'course_manage.html', context)


@staff_member_required
def course_analytics(request, course_id):
    """Список классов -> при выборе класса таблица: ученики x темы — сколько задач темы
    ученик решил на 10/10 (из скольких)."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    teacher = _teacher_or_none(request)
    if teacher is not None:
        classes = teacher.class_list()
    else:
        classes = list(
            Student.objects.order_by('school_class')
            .values_list('school_class', flat=True).distinct()
        )

    selected_class = normalize_school_class(request.GET.get('class', ''))
    if selected_class not in classes:
        selected_class = classes[0] if classes else ''

    topics = list(course.topics.prefetch_related('tasks').order_by('order', 'name'))
    topic_task_ids = [(topic, list(topic.tasks.values_list('id', flat=True))) for topic in topics]

    students = []
    if selected_class:
        class_students = Student.objects.filter(school_class=selected_class).order_by('full_name')

        # Задачи этого класса, решённые на 10/10 (итоговая оценка): {(student_id, task_id)}
        perfect = set(
            Submission.objects.filter(is_graded=True, score=10, student__school_class=selected_class)
            .values_list('student_id', 'task_id')
        )

        for student in class_students:
            cells = []
            overall_done = overall_total = 0
            for topic, task_ids in topic_task_ids:
                done = sum(1 for tid in task_ids if (student.id, tid) in perfect)
                cells.append({'topic': topic, 'done': done, 'total': len(task_ids)})
                overall_done += done
                overall_total += len(task_ids)
            students.append({
                'student': student,
                'cells': cells,
                'overall_done': overall_done,
                'overall_total': overall_total,
            })

    context = {
        'course': course,
        'classes': classes,
        'selected_class': selected_class,
        'topics': topics,
        'students': students,
    }
    return render(request, 'course_analytics.html', context)


@staff_member_required
def course_edit(request, course_id):
    """Изменение названия/описания/видимости курса."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            course.name = name
        course.description = request.POST.get('description', '').strip()
        course.is_visible = request.POST.get('is_visible') == 'on'
        # Классы вне списка учителя (их выбрал админ) остаются как были.
        allowed = set(_class_choices(request.user))
        kept = [c for c in course.visible_class_list() if c not in allowed]
        course.set_visible_classes(kept + _read_visible_classes(request))
        course.save()
        messages.success(request, 'Курс обновлён.')

    return redirect('course_manage', course_id=course.id)


@staff_member_required
def topic_create(request, course_id):
    """Добавление темы курса: название, необязательная теория (ссылка на YouTube), задачи."""
    course = _get_owned_course_or_none(request, course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    all_tasks = _tasks_by_level_and_solved(Task.objects.prefetch_related('tags'))

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Укажите название темы.')
        else:
            topic = Topic(
                course=course,
                name=name,
                order=request.POST.get('order') or 0,
                theory_video_url=request.POST.get('theory_video_url', '').strip(),
            )
            error = _apply_theory_upload(request, topic)
            topic.save()
            topic.tasks.set(request.POST.getlist('tasks'))
            if error:
                messages.error(request, f'Тема «{topic.name}» добавлена, но теория не загружена: {error}')
                return redirect('topic_edit', topic_id=topic.id)
            messages.success(request, f'Тема «{topic.name}» добавлена.')
            return redirect('course_manage', course_id=course.id)

    return render(request, 'topic_form.html', {
        'course': course, 'topic': None, 'all_tasks': all_tasks, 'selected_task_ids': set(),
        'all_tags': Tag.objects.all(),
    })


THEORY_MAX_BYTES = 2 * 1024 * 1024  # 2 МБ на HTML-файл теории


def _apply_theory_upload(request, topic):
    """Загрузка/удаление HTML-файла теории из формы темы. Возвращает текст ошибки или None."""
    if request.POST.get('remove_theory'):
        topic.theory_html, topic.theory_filename = '', ''
    upload = request.FILES.get('theory_file')
    if not upload:
        return None
    name = upload.name or 'theory.html'
    if not name.lower().endswith(('.html', '.htm')):
        return 'Теория должна быть файлом .html или .htm.'
    if upload.size > THEORY_MAX_BYTES:
        return 'Файл теории слишком большой (максимум 2 МБ).'
    raw = upload.read()
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return 'Не удалось прочитать файл теории — сохраните его в кодировке UTF-8.'
    topic.theory_html, topic.theory_filename = text, name[:255]
    return None


@login_required
@xframe_options_sameorigin
def topic_theory(request, topic_id):
    """HTML-теория темы. Показывается в песочнице (CSP sandbox, без allow-same-origin):
    даже если в загруженном файле есть скрипты, они работают с «чужого» происхождения и
    не видят сессию/cookie сайта. Открывается внутри страницы курса или в новой вкладке."""
    topic = get_object_or_404(Topic.objects.select_related('course'), id=topic_id)
    if not _course_open_for(request.user, topic.course):
        raise Http404
    if not topic.theory_html:
        raise Http404
    response = HttpResponse(topic.theory_html, content_type='text/html; charset=utf-8')
    response['Content-Security-Policy'] = 'sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox'
    return response


@staff_member_required
def topic_edit(request, topic_id):
    """Редактирование темы: название, теория, порядок, набор задач."""
    topic = get_object_or_404(Topic, id=topic_id)
    course = _get_owned_course_or_none(request, topic.course_id)
    if course is None:
        messages.error(request, 'Это не ваш курс.')
        return redirect('teacher_dashboard')

    all_tasks = _tasks_by_level_and_solved(Task.objects.prefetch_related('tags'))

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            topic.name = name
        topic.order = request.POST.get('order') or topic.order
        topic.theory_video_url = request.POST.get('theory_video_url', '').strip()
        error = _apply_theory_upload(request, topic)
        topic.save()
        topic.tasks.set(request.POST.getlist('tasks'))
        if error:
            messages.error(request, f'Тема сохранена, но теория не загружена: {error}')
            return redirect('topic_edit', topic_id=topic.id)
        messages.success(request, f'Тема «{topic.name}» обновлена.')
        return redirect('course_manage', course_id=course.id)

    return render(request, 'topic_form.html', {
        'course': course, 'topic': topic, 'all_tasks': all_tasks,
        'selected_task_ids': set(topic.tasks.values_list('id', flat=True)),
        'all_tags': Tag.objects.all(),
    })


@staff_member_required
def task_tags_list(request):
    """Полный список всех задач для управления тегами — доступен любому учителю,
    вне привязки к его классам или курсам."""
    tasks = _tasks_by_level_and_solved(Task.objects.prefetch_related('tags'))

    level_filter = request.GET.get('level', '')
    if level_filter in ['A', 'B', 'C']:
        tasks = tasks.filter(level=level_filter)

    q = request.GET.get('q', '').strip()
    if q:
        tasks = tasks.filter(title__icontains=q)

    return render(request, 'task_tags_list.html', {
        'tasks': tasks, 'level_filter': level_filter, 'q': q,
    })


@staff_member_required
def task_tags_edit(request, task_id):
    """Старый адрес страницы тегов — теперь всё редактируется на одной странице задачи."""
    return redirect('task_edit', task_id=task_id)


def _clean_text(value):
    """Текст из формы: переносы строк браузера (\\r\\n) -> \\n."""
    return (value or '').replace('\r\n', '\n').replace('\r', '\n')


def _tests_from_post(post):
    """Тест-кейсы из формы: пары полей test_input/test_output (в том же порядке).
    Строку, где пусты и ввод, и вывод, пропускаем (пустой вывод сам по себе допустим —
    бывают задачи, где в каком-то случае ничего не нужно печатать)."""
    tests = []
    for inp, out in zip(post.getlist('test_input'), post.getlist('test_output')):
        inp, out = _clean_text(inp), _clean_text(out).strip()
        if inp.strip() or out:
            tests.append({'input': inp, 'output': out})
    return tests


TASK_TEXT_FIELDS = ['title', 'description', 'input_example', 'output_example',
                    'title_kk', 'description_kk', 'input_example_kk', 'output_example_kk']
TASK_REQUIRED_FIELDS = {'title': 'Название', 'description': 'Условие',
                        'input_example': 'Пример ввода', 'output_example': 'Пример вывода'}
TASK_LEVELS = ('A', 'B', 'C')


def _task_form(request, task):
    """Общая форма задачи: создание (task ещё не сохранён) и редактирование.
    Условие, пример ввода/вывода, казахская версия, тесты для автопроверки, теги.
    Уровень выбирается только при создании — у существующей задачи его меняет админ
    (от уровня зависит коэффициент в лидерборде)."""
    is_new = task.pk is None

    if request.method == 'POST':
        values = {f: _clean_text(request.POST.get(f, '')) for f in TASK_TEXT_FIELDS}
        values['title'] = values['title'].strip()
        tests = _tests_from_post(request.POST)
        errors = [f'Поле «{label}» не может быть пустым.' for f, label in TASK_REQUIRED_FIELDS.items()
                  if not values[f].strip()]
        level = request.POST.get('level', '') if is_new else task.level
        if is_new and level not in TASK_LEVELS:
            errors.append('Выберите уровень задачи (A, B или C).')
        if len(tests) > MAX_TEST_CASES:
            errors.append(f'Не больше {MAX_TEST_CASES} тестов на задачу.')
        if values['title'] and Task.objects.filter(title=values['title']).exclude(pk=task.pk).exists():
            errors.append('Задача с таким названием уже есть.')

        if not errors:
            for f in TASK_TEXT_FIELDS:
                setattr(task, f, values[f])
            task.level = level
            task.test_cases = tests
            task.save()
            task.tags.set(request.POST.getlist('tags'))
            new_tag_name = request.POST.get('new_tag', '').strip()
            if new_tag_name:
                tag, _ = Tag.objects.get_or_create(name=new_tag_name)
                task.tags.add(tag)
            if is_new:
                messages.success(request, f'Задача «{task.title}» создана. Тестов: {len(tests)}. '
                                          'Она уже есть в каталоге «Все задачи».')
            else:
                messages.success(request, f'Задача «{task.title}» сохранена. Тестов: {len(tests)}. '
                                          'Новые тесты применяются к следующим отправкам — старые оценки не меняются.')
            return redirect('task_edit', task_id=task.id)

        for e in errors:
            messages.error(request, e)
        form_values = dict(values, level=level)
        selected_tag_ids = {int(t) for t in request.POST.getlist('tags') if t.isdigit()}
    else:
        form_values = {f: getattr(task, f) for f in TASK_TEXT_FIELDS}
        form_values['level'] = task.level
        tests = list(task.test_cases or [])
        selected_tag_ids = set(task.tags.values_list('id', flat=True)) if not is_new else set()

    # Всегда показываем хотя бы 5 строк для тестов (пустые можно просто не заполнять).
    test_rows = tests + [{'input': '', 'output': ''}] * max(0, 5 - len(tests))

    return render(request, 'task_edit.html', {
        'task': task,
        'is_new': is_new,
        'v': form_values,
        'test_rows': test_rows,
        'all_tags': Tag.objects.all(),
        'selected_tag_ids': selected_tag_ids,
        'max_tests': MAX_TEST_CASES,
    })


@staff_member_required
def task_edit(request, task_id):
    """Редактирование задачи учителем (любой учитель, любая задача)."""
    return _task_form(request, get_object_or_404(Task, id=task_id))


@staff_member_required
def task_create(request):
    """Новая задача через форму — доступно любому учителю."""
    return _task_form(request, Task(level=''))


TASK_IMPORT_MAX_BYTES = 1024 * 1024  # 1 МБ JSON
TASK_IMPORT_MAX_TASKS = 200


def _parse_tasks_json(data):
    """Проверка JSON для импорта задач. Принимает список задач (или одну задачу-объект).
    Возвращает (задачи, ошибки); если есть хоть одна ошибка — ничего не импортируем."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        return [], ['Ожидается список задач: [ {...}, {...} ].']
    if len(data) > TASK_IMPORT_MAX_TASKS:
        return [], [f'За раз можно загрузить не больше {TASK_IMPORT_MAX_TASKS} задач.']

    existing = set(Task.objects.values_list('title', flat=True))
    seen, items, errors = set(), [], []
    for i, raw in enumerate(data, 1):
        where = f'Задача №{i}'
        if not isinstance(raw, dict):
            errors.append(f'{where}: должна быть объектом {{...}}.'); continue
        item = {f: _clean_text(str(raw.get(f) or '')) for f in TASK_TEXT_FIELDS}
        item['title'] = item['title'].strip()
        if item['title']:
            where = f'Задача №{i} «{item["title"]}»'
        for f, label in TASK_REQUIRED_FIELDS.items():
            if not item[f].strip():
                errors.append(f'{where}: не заполнено поле «{f}» ({label}).')
        level = str(raw.get('level') or '').strip().upper()
        if level not in TASK_LEVELS:
            errors.append(f'{where}: поле «level» должно быть A, B или C.')
        item['level'] = level
        if item['title'] in existing:
            errors.append(f'{where}: задача с таким названием уже есть на сайте.')
        elif item['title'] in seen:
            errors.append(f'{where}: название повторяется в файле.')
        seen.add(item['title'])

        tags = raw.get('tags') or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            errors.append(f'{where}: «tags» — список названий тегов, например ["if/else"].'); tags = []
        item['tags'] = [t.strip() for t in tags if t.strip()]

        tests = raw.get('test_cases') or []
        clean_tests = []
        if not isinstance(tests, list):
            errors.append(f'{where}: «test_cases» — список вида [{{"input": "...", "output": "..."}}].')
        else:
            for j, t in enumerate(tests, 1):
                if not isinstance(t, dict) or 'input' not in t or 'output' not in t:
                    errors.append(f'{where}: тест №{j} должен содержать "input" и "output".'); continue
                clean_tests.append({'input': _clean_text(str(t['input'])), 'output': _clean_text(str(t['output'])).strip()})
            if len(clean_tests) > MAX_TEST_CASES:
                errors.append(f'{where}: не больше {MAX_TEST_CASES} тестов.')
        item['test_cases'] = clean_tests
        items.append(item)
    return items, errors


@staff_member_required
def task_import(request):
    """Импорт задач из JSON (файлом или вставкой текста) — доступно любому учителю.
    Всё или ничего: при любой ошибке ни одна задача не создаётся."""
    pasted = ''
    if request.method == 'POST':
        upload = request.FILES.get('json_file')
        pasted = request.POST.get('json_text', '')
        raw = None
        if upload:
            if upload.size > TASK_IMPORT_MAX_BYTES:
                messages.error(request, 'Файл слишком большой (максимум 1 МБ).')
            else:
                raw = upload.read()
        elif pasted.strip():
            raw = pasted.encode('utf-8')
        else:
            messages.error(request, 'Выберите JSON-файл или вставьте JSON в поле.')

        if raw is not None:
            try:
                data = json.loads(raw.decode('utf-8-sig'))
            except (UnicodeDecodeError, ValueError) as exc:
                messages.error(request, f'Это не похоже на правильный JSON: {exc}')
            else:
                items, errors = _parse_tasks_json(data)
                if errors:
                    for e in errors[:30]:
                        messages.error(request, e)
                    if len(errors) > 30:
                        messages.error(request, f'…и ещё ошибок: {len(errors) - 30}.')
                    messages.warning(request, 'Ничего не импортировано — исправьте ошибки и загрузите снова.')
                else:
                    with transaction.atomic():
                        for item in items:
                            task = Task.objects.create(**{k: v for k, v in item.items() if k != 'tags'})
                            for name in item['tags']:
                                tag, _ = Tag.objects.get_or_create(name=name)
                                task.tags.add(tag)
                    with_tests = sum(1 for it in items if it['test_cases'])
                    messages.success(request, f'Импортировано задач: {len(items)} (с тестами: {with_tests}).')
                    return redirect('task_tags_list')

    return render(request, 'task_import.html', {
        'pasted': pasted, 'max_tasks': TASK_IMPORT_MAX_TASKS, 'max_tests': MAX_TEST_CASES,
    })


def _managed_classes(user):
    """Классы, учеников которых этот пользователь может видеть и редактировать:
    для учителя — только его классы (Teacher.classes), для админа — None (все)."""
    if user.is_superuser:
        return None
    teacher = Teacher.objects.filter(user=user).first()
    return None if teacher is None else teacher.class_list()


def _managed_student_or_none(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    classes = _managed_classes(request.user)
    if classes is not None and student.school_class not in classes:
        return None
    return student


def _student_form_classes(request):
    """Классы для выбора в форме ученика: у учителя — список его классов (select),
    у админа — None (свободный ввод)."""
    classes = _managed_classes(request.user)
    if classes is None:
        return None
    return sorted(set(classes))


@staff_member_required
def student_list(request):
    from .moderation_views import release_expired_blocks
    release_expired_blocks()
    """Ученики для редактирования: учитель видит только свои классы, админ — всех."""
    classes = _managed_classes(request.user)
    students = Student.objects.select_related('user').order_by('school_class', 'full_name')
    if classes is not None:
        students = students.filter(school_class__in=classes)

    class_filter = normalize_school_class(request.GET.get('class', ''))
    if class_filter:
        students = students.filter(school_class=class_filter)

    q = request.GET.get('q', '').strip()
    if q:
        students = students.filter(Q(full_name__icontains=q) | Q(user__username__icontains=q))

    if classes is not None:
        all_classes = sorted(set(classes))
    else:
        all_classes = Student.objects.order_by('school_class').values_list('school_class', flat=True).distinct()

    return render(request, 'student_list.html', {
        'students': students, 'all_classes': all_classes, 'class_filter': class_filter, 'q': q,
        'my_classes': classes,
    })


@staff_member_required
def student_create(request):
    """Добавление нового ученика (логин, пароль, имя, класс). Учитель — только в свои классы."""
    form_classes = _student_form_classes(request)
    if form_classes == []:
        messages.error(request, 'Вам пока не назначены классы — обратитесь к администратору.')
        return redirect('student_list')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        full_name = request.POST.get('full_name', '').strip()
        school_class = normalize_school_class(request.POST.get('school_class', ''))

        if not username or not password or not full_name or not school_class:
            messages.error(request, 'Заполните логин, пароль, имя и класс.')
        elif form_classes is not None and school_class not in form_classes:
            messages.error(request, 'Можно добавлять учеников только в свои классы.')
        elif User.objects.filter(username=username).exists():
            messages.error(request, 'Этот логин уже занят.')
        else:
            user = User.objects.create_user(username=username, password=password)
            Student.objects.create(user=user, full_name=full_name, school_class=school_class)
            messages.success(request, f'Ученик «{full_name}» добавлен.')
            return redirect('student_list')

    return render(request, 'student_form.html', {'mode': 'create', 'form_classes': form_classes})


@staff_member_required
def student_edit(request, student_id):
    """Изменение логина, пароля, имени и класса ученика — только ученики своих классов."""
    student = _managed_student_or_none(request, student_id)
    if student is None:
        messages.error(request, 'Это ученик не вашего класса.')
        return redirect('student_list')
    form_classes = _student_form_classes(request)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        full_name = request.POST.get('full_name', '').strip()
        school_class = normalize_school_class(request.POST.get('school_class', ''))

        if not username or not full_name or not school_class:
            messages.error(request, 'Заполните логин, имя и класс.')
        elif form_classes is not None and school_class not in form_classes:
            messages.error(request, 'Можно переводить ученика только в свои классы.')
        elif User.objects.filter(username=username).exclude(id=student.user_id).exists():
            messages.error(request, 'Этот логин уже занят другим пользователем.')
        else:
            student.user.username = username
            if password:
                student.user.set_password(password)
            student.user.save()
            student.full_name = full_name
            student.school_class = school_class
            student.save()
            messages.success(request, 'Данные ученика обновлены.')
            return redirect('student_list')

    return render(request, 'student_form.html', {'mode': 'edit', 'student': student, 'form_classes': form_classes})


@staff_member_required
def student_delete(request, student_id):
    """Удаление ученика вместе с его аккаунтом и решениями — только ученики своих классов."""
    student = _managed_student_or_none(request, student_id)
    if student is None:
        messages.error(request, 'Это ученик не вашего класса.')
        return redirect('student_list')
    if request.method == 'POST':
        name = student.full_name
        student.user.delete()  # каскадом удаляет Student и его Submission
        messages.success(request, f'Ученик «{name}» удалён.')
    return redirect('student_list')


@login_required
def topic_video(request, topic_id):
    """Отдельная страница с видеоуроком темы — открывается по клику с страницы курса,
    чтобы само видео не занимало много места в списке задач."""
    topic = get_object_or_404(Topic, id=topic_id)
    course = topic.course
    if not _course_open_for(request.user, course):
        messages.warning(request, 'Этот курс пока недоступен.')
        return redirect('courses')
    if not topic.theory_video_url:
        messages.info(request, 'Для этой темы видеоурок пока не добавлен.')
        return redirect('course_detail', course_id=course.id)

    return render(request, 'topic_video.html', {'topic': topic, 'course': course})


def _pending_queue(user):
    """Очередь решений на проверку для этого учителя (только его классы; админ — все),
    в том же порядке, что и в кабинете преподавателя: сначала самые давние."""
    teacher = None if user.is_superuser else Teacher.objects.filter(user=user).first()
    pending = Submission.objects.filter(status='TESTING')
    if teacher is not None:
        pending = pending.filter(student__school_class__in=teacher.class_list())
    return pending.order_by('updated_at')


@staff_member_required
def teacher_dashboard(request):
    """Кабинет преподавателя: очередь на проверку и последние проверенные."""
    teacher = None if request.user.is_superuser else Teacher.objects.filter(user=request.user).first()

    pending_submissions = Submission.objects.filter(status='TESTING')
    graded_submissions = Submission.objects.filter(status='DONE')
    my_courses = Course.objects.all()

    if teacher is not None:
        # Учитель видит только очередь и статистику по своим классам и курсам.
        class_list = teacher.class_list()
        pending_submissions = pending_submissions.filter(student__school_class__in=class_list)
        graded_submissions = graded_submissions.filter(student__school_class__in=class_list)
        my_courses = teacher.courses.all()

    pending_submissions = pending_submissions.order_by('updated_at')
    graded_submissions = graded_submissions.order_by('-updated_at')[:20]

    from .duels import duels_mode
    context = {
        'pending_submissions': pending_submissions,
        'graded_submissions': graded_submissions,
        'teacher': teacher,
        'my_courses': my_courses,
    }
    context['duels_mode'] = duels_mode()
    return render(request, 'teacher_dashboard.html', context)


@staff_member_required
def grade_submission(request, submission_id):
    """Страница проверки конкретного решения."""
    submission = get_object_or_404(Submission, id=submission_id)

    # Учитель проверяет только заявки своих классов — чужие ему недоступны.
    if not request.user.is_superuser:
        teacher = Teacher.objects.filter(user=request.user).first()
        if teacher is not None and submission.student.school_class not in teacher.class_list():
            messages.error(request, 'Это не ваш класс — проверка недоступна.')
            return redirect('teacher_dashboard')

    # Оцениваем самую свежую ещё не проверенную попытку; если все уже проверены —
    # последнюю (повторная проверка/исправление оценки).
    grading_attempt = (
        submission.attempts.filter(score__isnull=True).order_by('-number').first()
        or submission.attempts.order_by('-number').first()
    )

    if request.method == 'POST':
        try:
            score = max(0, min(10, int(request.POST.get('score', ''))))
        except (TypeError, ValueError):
            messages.error(request, 'Оценка должна быть числом от 0 до 10.')
            return redirect('grade_submission', submission_id=submission.id)
        comment = request.POST.get('comment', '')

        if grading_attempt is None:
            # Старая отправка без истории попыток — заводим попытку из текущего кода.
            grading_attempt = Attempt.objects.create(
                submission=submission, number=1, code=submission.code or '',
                submitted_at=submission.last_submitted_at or submission.updated_at,
            )
        grading_attempt.score = score
        grading_attempt.teacher_comment = comment
        grading_attempt.auto_graded = False  # учитель поставил/исправил оценку вручную
        grading_attempt.graded_at = timezone.now()
        grading_attempt.save()

        # Итоговая оценка по задаче = максимум среди всех проверенных попыток.
        final_score = submission.recalc_final_score()
        from . import quarters as _quarters
        _quarters.invalidate()
        submission.teacher_comment = comment
        submission.status = 'DONE'
        submission.save()

        messages.success(
            request,
            f'Оценка {score}/10 за попытку #{grading_attempt.number} сохранена. '
            f'Итоговая оценка (лучшая из попыток): {final_score}/10.'
        )

        # Кнопка "Проверить следующего": сохраняем и сразу открываем следующее решение
        # из очереди (текущее уже DONE, поэтому в очередь не попадает).
        if request.POST.get('next'):
            next_submission = _pending_queue(request.user).first()
            if next_submission is not None:
                return redirect('grade_submission', submission_id=next_submission.id)
            messages.info(request, 'Очередь проверки пуста — все решения проверены.')
        return redirect('teacher_dashboard')

    # Подстраховка: если автотесты ещё не бегали для этой отправки (например, решение
    # существует с тех пор, как эта функция появилась) — прогоняем прямо сейчас,
    # перед тем как показать страницу учителю.
    if submission.task.test_cases and _autotest_is_stale(submission):
        _refresh_autotests(submission)

    return render(request, 'grade_submission.html', {
        'submission': submission,
        'grading_attempt': grading_attempt,
        'attempts': submission.attempts.all(),
        # Сколько решений в очереди останется после этого (без текущего).
        'queue_left': _pending_queue(request.user).exclude(id=submission.id).count(),
    })

def web_manifest(request):
    """Манифест веб-приложения: иконка и название при добавлении сайта на экран телефона."""
    from django.templatetags.static import static
    data = {
        'name': 'Pifagor Contest',
        'short_name': 'Pifagor',
        'description': 'Задачи по программированию, курсы и экзамены школы Pifagor',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#ffffff',
        'theme_color': '#1a1a4b',
        'icons': [
            {'src': static('icons/icon-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': static('icons/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': static('icons/icon-maskable-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    }
    return HttpResponse(json.dumps(data, ensure_ascii=False), content_type='application/manifest+json')


def favicon(request):
    from django.templatetags.static import static
    return redirect(static('icons/favicon.ico'))
