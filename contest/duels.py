# -*- coding: utf-8 -*-
"""Дуэли: два ученика, 3 случайные задачи, первое 10/10 на задаче даёт очко, матч до 2 очков.
Рейтинг — как в шахматах (Эло), у всех старт 800.

Правила:
- у каждого игрока 3 попытки на каждую задачу;
- «10/10» = все тесты задачи пройдены; кто первым — тот забирает очко, задача закрывается;
- матч заканчивается, когда кто-то набрал 2 очка, или все задачи закрыты (очко взято или у
  обоих кончились попытки). Если очков поровну — ничья;
- задачи открываются по одной: следующая появляется, когда предыдущая закрыта;
- на каждую задачу 5 минут: не успели — задача закрывается без очка и открывается следующая;
- «Сдаться» отдаёт победу сопернику; уход со страницы дуэли (другая вкладка, свёрнутое окно)
  — сразу поражение;
- лимита времени нет, но если 20 минут никто ничего не отправлял, матч закрывается сам:
  побеждает тот, у кого больше очков (поровну — ничья).
"""
import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .autotest import run_autotests
from .models import (Duel, DuelAttempt, DuelQueueEntry, DuelTask, SiteSettings, Student,
                     Submission, Task, normalize_school_class)

START_RATING = 800
RATING_FLOOR = 100
K_FACTOR = 32
POINTS_TO_WIN = 2
TASKS_PER_DUEL = 3
ATTEMPTS_PER_TASK = 3
TASK_TIME_LIMIT = timedelta(minutes=5)     # время на каждую задачу
INACTIVITY = timedelta(minutes=20)
QUEUE_TTL = timedelta(minutes=10)


# ------------------------------------------------------------------ Эло
def expected_score(rating, opponent_rating):
    return 1 / (1 + 10 ** ((opponent_rating - rating) / 400))


def rating_delta(rating, opponent_rating, score):
    """score: 1 — победа, 0.5 — ничья, 0 — поражение."""
    return round(K_FACTOR * (score - expected_score(rating, opponent_rating)))


# ------------------------------------------------------------------ допуск
def duels_mode():
    return SiteSettings.get().duels_mode


def duels_enabled():
    return duels_mode() != 'off'


def is_duels_admin(user):
    """Преподаватель, который управляет дуэлями: суперпользователь или логин из DUELS_ADMIN_USERNAMES."""
    from django.conf import settings
    return bool(user and user.is_authenticated and user.is_staff and (
        user.is_superuser or user.username in settings.DUELS_ADMIN_USERNAMES))


def teacher_sees_duels(user):
    """Страницу «Дуэли» видит любой преподаватель: там он открывает дуэли своим классам."""
    return bool(user and user.is_authenticated and user.is_staff)


def open_classes():
    """Классы, которым преподаватели открыли дуэли."""
    return SiteSettings.get().duel_class_list()


def visible_to(student):
    """Видны ли дуэли ученику. «Выключены» — никому. Иначе: режим «для всех», либо его класс
    открыт преподавателем, либо режим «только тестеры» и он тестер."""
    if student is None:
        return False
    obj = SiteSettings.get()
    if obj.duels_mode == 'off':
        return False
    if obj.duels_mode == 'all' or normalize_school_class(student.school_class) in obj.duel_class_list():
        return True
    return obj.duels_mode == 'testers' and student.duel_tester


def can_play(student):
    """(можно ли, причина) — дуэли видны ученику и он не в списке подозрительных."""
    if not visible_to(student):
        return False, 'Дуэли сейчас выключены преподавателем.'
    from .moderation_views import _reasons
    if _reasons(Student.objects.filter(id=student.id)):
        return False, 'Для вашего аккаунта дуэли недоступны — обратитесь к преподавателю.'
    return True, ''


def active_duel_for(student):
    duel = Duel.objects.filter(status='ACTIVE').filter(
        player1=student).first() or Duel.objects.filter(status='ACTIVE', player2=student).first()
    if duel is not None:
        close_if_inactive(duel)
        duel.refresh_from_db()
        if duel.status != 'ACTIVE':
            return None
    return duel


# ------------------------------------------------------------------ очередь и подбор
def join_queue(student):
    entry, _ = DuelQueueEntry.objects.get_or_create(student=student, defaults={'rating': student.duel_rating})
    if entry.rating != student.duel_rating:
        entry.rating = student.duel_rating
        entry.save(update_fields=['rating'])
    return entry


def leave_queue(student):
    DuelQueueEntry.objects.filter(student=student).delete()


def try_match(student):
    """Если соперник нашёлся — создаёт дуэль и возвращает её, иначе None.
    Допустимая разница рейтингов растёт, пока игрок ждёт: 100 + 10 за каждую секунду."""
    now = timezone.now()
    with transaction.atomic():
        DuelQueueEntry.objects.filter(joined_at__lt=now - QUEUE_TTL).delete()
        mine = DuelQueueEntry.objects.select_for_update().filter(student=student).first()
        if mine is None:
            return None
        waited = (now - mine.joined_at).total_seconds()
        window = 100 + 10 * waited
        others = list(DuelQueueEntry.objects.select_for_update()
                      .exclude(student=student).select_related('student'))
        others = [o for o in others if abs(o.rating - mine.rating) <= max(
            window, 100 + 10 * (now - o.joined_at).total_seconds())]
        if not others:
            return None
        other = min(others, key=lambda o: abs(o.rating - mine.rating))
        tasks = pick_tasks(student, other.student)
        if len(tasks) < TASKS_PER_DUEL:
            return None
        p1, p2 = (mine.student, other.student)
        duel = Duel.objects.create(
            player1=p1, player2=p2, p1_rating_before=p1.duel_rating, p2_rating_before=p2.duel_rating,
            last_activity=now)
        for i, task in enumerate(tasks):
            DuelTask.objects.create(duel=duel, task=task, order=i, opened_at=now if i == 0 else None)
        DuelQueueEntry.objects.filter(student__in=[p1, p2]).delete()
        return duel


def levels_for(avg_rating):
    if avg_rating < 1000:
        return ['A']
    if avg_rating < 1200:
        return ['A', 'B']
    return ['B', 'C']


def pick_tasks(a, b):
    """3 случайные задачи с автотестами подходящего уровня; сначала те, которые оба ещё не
    решали на 10/10 (если таких не хватает — добираем остальные)."""
    levels = levels_for((a.duel_rating + b.duel_rating) / 2)
    pool = [(tid, lvl) for tid, lvl, tc in Task.objects.values_list('id', 'level', 'test_cases') if tc]
    chosen = [tid for tid, lvl in pool if lvl in levels]
    if len(chosen) < TASKS_PER_DUEL:
        chosen = [tid for tid, _ in pool]
    solved = set(Submission.objects.filter(student__in=[a, b], is_graded=True, score=10)
                 .values_list('task_id', flat=True))
    fresh = [t for t in chosen if t not in solved]
    random.shuffle(fresh)
    picked = fresh[:TASKS_PER_DUEL]
    if len(picked) < TASKS_PER_DUEL:
        rest = [t for t in chosen if t not in picked]
        random.shuffle(rest)
        picked += rest[:TASKS_PER_DUEL - len(picked)]
    order = {t.id: t for t in Task.objects.filter(id__in=picked)}
    return [order[i] for i in picked if i in order]


# ------------------------------------------------------------------ ход игры
def attempts_left(duel_task, student):
    used = duel_task.attempts.filter(player=student).count()
    return max(ATTEMPTS_PER_TASK - used, 0)


def task_closed(duel_task, duel):
    """Задача закрыта: очко уже взято или у обоих кончились попытки."""
    if duel_task.winner_id or duel_task.timed_out:
        return True
    return all(attempts_left(duel_task, p) == 0 for p in duel.players())


def advance(duel, now=None):
    """Проставляет времена открытия/закрытия задач и закрывает просроченные.
    Вызывать под блокировкой дуэли. Возвращает текущую (открытую) задачу или None."""
    now = now or timezone.now()
    prev_close = duel.created_at
    for dt in duel.duel_tasks.order_by('order'):
        if dt.closed_at is None:
            if dt.opened_at is None:
                dt.opened_at = prev_close
            deadline = dt.opened_at + TASK_TIME_LIMIT
            if dt.winner_id:
                dt.closed_at = dt.solved_at or now
            elif all(attempts_left(dt, p) == 0 for p in duel.players()):
                last = dt.attempts.order_by('-created_at').first()
                dt.closed_at = last.created_at if last else now
            elif now >= deadline:
                dt.timed_out = True
                dt.closed_at = deadline
            else:
                dt.save(update_fields=['opened_at'])
                return dt
            dt.save(update_fields=['opened_at', 'closed_at', 'timed_out'])
        prev_close = dt.closed_at
    return None


def refresh(duel):
    """Актуализирует дуэль: закрывает просроченные задачи и, если всё закончилось, саму дуэль."""
    with transaction.atomic():
        locked = Duel.objects.select_for_update().get(pk=duel.pk)
        if locked.status == 'ACTIVE':
            advance(locked)
            _maybe_finish(locked)
        return locked


def seconds_left(dt, now=None):
    """Сколько секунд осталось на открытую задачу (0 — время вышло)."""
    now = now or timezone.now()
    if dt.opened_at is None:
        return int(TASK_TIME_LIMIT.total_seconds())
    return max(int((dt.opened_at + TASK_TIME_LIMIT - now).total_seconds()), 0)


def current_task(duel):
    """Задача, которая сейчас открыта: первая ещё не закрытая (или None, если все закрыты)."""
    for dt in duel.duel_tasks.order_by('order'):
        if not task_closed(dt, duel):
            return dt
    return None


def submit_code(duel, student, duel_task, code):
    """Прогоняет код и фиксирует попытку. Возвращает (ok, сообщение, результат|None)."""
    duel = refresh(duel)
    duel_task = DuelTask.objects.get(pk=duel_task.pk)
    if duel.status != 'ACTIVE':
        return False, 'Дуэль уже завершена.', None
    if not duel.has_player(student) or duel_task.duel_id != duel.id:
        return False, 'Это не ваша дуэль.', None
    if not (code or '').strip():
        return False, 'Вставьте код решения.', None
    if duel_task.winner_id:
        return False, 'Эта задача уже закрыта — очко взято.', None
    current = current_task(duel)
    if current is None or current.id != duel_task.id:
        return False, 'Эта задача сейчас не открыта.', None
    if attempts_left(duel_task, student) == 0:
        return False, 'Попытки на эту задачу закончились.', None

    result = run_autotests(duel_task.task, code)     # вне блокировки: может идти до нескольких секунд
    if not result.get('available'):
        return False, 'У этой задачи нет автотестов.', None
    passed, total = result['passed'], result['total']
    full = total > 0 and passed == total

    with transaction.atomic():
        locked = Duel.objects.select_for_update().get(pk=duel.pk)
        dt = DuelTask.objects.select_for_update().get(pk=duel_task.pk)
        if locked.status != 'ACTIVE':
            return False, 'Дуэль уже завершена.', None
        if dt.winner_id or dt.timed_out or attempts_left(dt, student) == 0:
            return False, 'Эта задача уже закрыта.', None
        from .views import autotest_score
        DuelAttempt.objects.create(duel_task=dt, player=student, code=code, score=autotest_score(passed, total),
                                   tests_passed=passed, tests_total=total)
        locked.last_activity = timezone.now()
        won_point = False
        if full:
            dt.winner = student
            dt.solved_at = timezone.now()
            dt.save(update_fields=['winner', 'solved_at'])
            if student.id == locked.player1_id:
                locked.p1_points += 1
            else:
                locked.p2_points += 1
            won_point = True
        locked.save()
        dt.refresh_from_db()
        advance(locked)
        _maybe_finish(locked)
    msg = ('Верно! Вы получили очко.' if won_point
           else f'Пройдено тестов: {passed} из {total}. Попыток осталось: {attempts_left(dt, student)}.')
    return True, msg, result


def _maybe_finish(duel):
    """Завершает дуэль, если выполнено условие окончания (вызывать под блокировкой duel)."""
    if duel.status != 'ACTIVE':
        return
    if duel.p1_points >= POINTS_TO_WIN or duel.p2_points >= POINTS_TO_WIN:
        _finish(duel, _leader(duel), 'score')
        return
    if all(task_closed(dt, duel) for dt in duel.duel_tasks.all()):
        _finish(duel, _leader(duel), 'all_closed')


def _leader(duel):
    if duel.p1_points > duel.p2_points:
        return duel.player1
    if duel.p2_points > duel.p1_points:
        return duel.player2
    return None


def _finish(duel, winner, reason):
    """Закрывает дуэль и пересчитывает рейтинги (один раз — повторный вызов ничего не делает)."""
    if duel.status != 'ACTIVE':
        return
    p1, p2 = duel.player1, duel.player2
    s1 = 0.5 if winner is None else (1 if winner.id == p1.id else 0)
    d1 = rating_delta(p1.duel_rating, p2.duel_rating, s1)
    d2 = rating_delta(p2.duel_rating, p1.duel_rating, 1 - s1)
    new1 = max(p1.duel_rating + d1, RATING_FLOOR)
    new2 = max(p2.duel_rating + d2, RATING_FLOOR)
    duel.p1_rating_change, duel.p2_rating_change = new1 - p1.duel_rating, new2 - p2.duel_rating
    Student.objects.filter(pk=p1.pk).update(duel_rating=new1, duel_games=p1.duel_games + 1)
    Student.objects.filter(pk=p2.pk).update(duel_rating=new2, duel_games=p2.duel_games + 1)
    duel.status = 'FINISHED'
    duel.winner = winner
    duel.end_reason = reason
    duel.finished_at = timezone.now()
    duel.save()


def resign(duel, student, reason='resign'):
    """Игрок сдался (reason='resign') или покинул страницу (reason='left') — победа сопернику."""
    with transaction.atomic():
        locked = Duel.objects.select_for_update().get(pk=duel.pk)
        if locked.status == 'ACTIVE' and locked.has_player(student):
            _finish(locked, locked.opponent_of(student), reason)


def close_if_inactive(duel):
    if duel.status == 'ACTIVE':
        refresh(duel)
        duel.refresh_from_db()
    if duel.status == 'ACTIVE' and timezone.now() - duel.last_activity > INACTIVITY:
        with transaction.atomic():
            locked = Duel.objects.select_for_update().get(pk=duel.pk)
            if locked.status == 'ACTIVE':
                _finish(locked, _leader(locked), 'inactive')
