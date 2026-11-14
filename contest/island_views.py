# -*- coding: utf-8 -*-
"""«Битва за остров»: страницы учителя (создание, лобби, живая карта) и ученика (лобби, игра)."""
import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import codegame as cg
from . import island
from .codegame_views import _can_edit, _int, _my_classes
from .models import IslandGame, IslandPlayer, Student, Tag


def _lang(request):
    return 'kk' if request.session.get('content_lang') == 'kk' else 'ru'


def _client(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def _my_games(user):
    return IslandGame.objects.all() if user.is_superuser else IslandGame.objects.filter(owner=user)


# ------------------------------------------------------------------ учитель
def _save_form(request, game):
    game.mode = 'solo' if request.POST.get('mode') == 'solo' else 'teams'
    game.teams_count = _int(request.POST.get('teams_count'), 4, 2, island.MAX_TEAMS)
    game.minutes = _int(request.POST.get('minutes'), 40, 5, 90)
    topics = [t for t in request.POST.getlist('topics') if t in cg.TOPIC_LABELS]
    game.topics = ','.join(topics or list(cg.TOPIC_LABELS))
    mine = {c for c, _ in _my_classes(request.user)}
    game.set_classes({c for c in request.POST.getlist('classes') if c in mine})
    game.title = ' · '.join(x for x in ['Битва за остров', ', '.join(game.class_list())] if x)[:120]
    game.save()
    game.tags.set(Tag.visible().filter(id__in=[int(x) for x in request.POST.getlist('tags') if x.isdigit()]))


@staff_member_required
def manage(request):
    if request.method == 'POST':
        game = IslandGame(owner=request.user)
        _save_form(request, game)
        if not game.class_list():
            game.delete()
            messages.error(request, 'Выберите хотя бы один класс.')
            return redirect('island_manage')
        messages.success(request, 'Игра создана. Ученики могут входить, потом нажмите «Начать игру».')
        return redirect('island_control', game_id=game.id)
    return render(request, 'island_manage.html', {
        'games': _my_games(request.user).prefetch_related('players'), 'topics': cg.TOPICS,
        'classes': _my_classes(request.user), 'tags': Tag.visible(), 'max_teams': island.MAX_TEAMS,
        'max_exits': island.MAX_EXITS, 'prizes': island.PRIZES,
    })


def _teacher_game(request, game_id):
    game = get_object_or_404(IslandGame, id=game_id)
    if not _can_edit(request.user, game):
        raise Http404
    return game


@staff_member_required
def control(request, game_id):
    """Экран учителя: лобби с командами, потом живая карта для проектора, в конце — итоги."""
    game = _teacher_game(request, game_id)
    return render(request, 'island_control.html', {
        'game': game, 'team_slots': range(min(game.teams_count, island.MAX_TEAMS)), 'max_exits': island.MAX_EXITS,
        'team_names': json.dumps(island.TEAM_NAMES, ensure_ascii=False), 'team_colors': json.dumps(island.TEAM_COLORS),
    })


@staff_member_required
def control_state(request, game_id):
    game = _teacher_game(request, game_id)
    since = _int(request.GET.get('v'), 0, 0, 10 ** 9)
    return JsonResponse(island.state(game, since, lang=_lang(request)))


@staff_member_required
@require_POST
def control_action(request, game_id):
    game = _teacher_game(request, game_id)
    act = request.POST.get('action')
    if act == 'shuffle' and game.status == 'lobby':
        island.shuffle_teams(game)
    elif act == 'team' and game.status == 'lobby':
        p = get_object_or_404(IslandPlayer, id=request.POST.get('player'), game=game)
        island.set_team(game, p, _int(request.POST.get('team'), 0, 0, island.MAX_TEAMS - 1))
    elif act == 'remove' and game.status == 'lobby':
        IslandPlayer.objects.filter(id=request.POST.get('player'), game=game).delete()
        island._bump(game)
    elif act == 'start' and game.status == 'lobby':
        if not game.players.exists():
            return JsonResponse({'ok': False, 'error': 'Пока никто не вошёл в игру.'})
        island.start(game)
    elif act == 'finish' and game.status == 'running':
        island.finish_if_needed(game, force=True)
    elif act == 'restore':
        p = get_object_or_404(IslandPlayer, id=request.POST.get('player'), game=game)
        island.restore(p)
    elif act == 'delete':
        game.delete()
        messages.success(request, 'Игра удалена.')
        return JsonResponse({'ok': True, 'redirect': True})
    return JsonResponse({'ok': True})


# ------------------------------------------------------------------ ученик
def _student(request):
    st = Student.objects.filter(user=request.user).first()
    if st is None:
        raise Http404
    return st


def _player_game(request, game_id):
    """Игра, открытая классу ученика, и его игрок в ней (None, если ещё не вошёл)."""
    st = _student(request)
    game = get_object_or_404(IslandGame, id=game_id)
    player = IslandPlayer.objects.filter(game=game, student=st).first()
    if player is None and st.school_class not in game.class_list():
        raise Http404
    return st, game, player


@login_required
def play(request, game_id):
    """Одна страница на всю игру: лобби → карта → итоги. Вход на страницу = вход в игру."""
    if request.user.is_staff:
        return redirect('island_control', game_id=game_id)
    st, game, player = _player_game(request, game_id)
    game = island.finish_if_needed(game)
    if player is None:
        if game.status == 'finished':
            messages.info(request, 'Эта игра уже закончилась.')
            return redirect('codegames')
        player = island.join(game, st)
        game.refresh_from_db()
    elif game.status == 'running' and not player.kicked and player.client == _client(request):
        player = island.count_exit(player, 'reload')        # страницу обновили во время игры — это выход
    return render(request, 'island_play.html', {
        'game': game, 'player': player, 'max_exits': island.MAX_EXITS, 'cooldown': island.COOLDOWN,
        'i18n': json.dumps(_texts(_lang(request)), ensure_ascii=False),
    })


@login_required
def play_state(request, game_id):
    st, game, player = _player_game(request, game_id)
    if player is None:
        raise Http404
    since = _int(request.GET.get('v'), 0, 0, 10 ** 9)
    data = island.state(game, since, me=player, lang=_lang(request))
    if since == 0 and player.challenge:
        data['challenge'] = island.public_challenge(player, _lang(request))
    return JsonResponse(data)


def _armed(request, player):
    return bool(player.client) and player.client == _client(request)


@login_required
@require_POST
def play_arm(request, game_id):
    """Браузер вошёл в полный экран: игра привязывается к нему, ходы принимаются только из него."""
    st, game, player = _player_game(request, game_id)
    if player is None:
        raise Http404
    me = _client(request)
    IslandPlayer.objects.filter(pk=player.pk, client='').update(client=me)
    player.refresh_from_db()
    return JsonResponse({'ok': player.client == me and not player.kicked})


@login_required
@require_POST
def play_exit(request, game_id):
    st, game, player = _player_game(request, game_id)
    if player is None:
        raise Http404
    if game.status == 'running' and _armed(request, player):
        kind = request.POST.get('kind')
        player = island.count_exit(player, kind if kind in ('fullscreen', 'blur', 'hidden') else 'fullscreen')
    return JsonResponse({'exits': player.exits, 'left': max(island.MAX_EXITS - player.exits, 0), 'kicked': player.kicked})


@login_required
@require_POST
def play_move(request, game_id):
    st, game, player = _player_game(request, game_id)
    if player is None or not _armed(request, player):
        return JsonResponse({'error': 'notarmed'})
    res = island.move(game, player, _int(request.POST.get('x'), -1, -1, 999), _int(request.POST.get('y'), -1, -1, 999))
    if res.get('challenge'):
        res['challenge'] = island.public_challenge(player, _lang(request))
    res['me'] = {'x': player.x, 'y': player.y}
    return JsonResponse(res)


@login_required
@require_POST
def play_answer(request, game_id):
    st, game, player = _player_game(request, game_id)
    if player is None or not _armed(request, player):
        return JsonResponse({'error': 'notarmed'})
    res = island.answer(game, player, request.POST.get('value', ''))
    res['me'] = {'x': player.x, 'y': player.y}
    return JsonResponse(res)


def _texts(lang):
    """Надписи игры для JS — на языке ученика."""
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    ru = {
        'lobby': 'Ждём, пока учитель начнёт игру', 'joined': 'В игре', 'noteam': 'команда появится перед стартом',
        'start_fs': 'Начать в полноэкранном режиме', 'back_fs': 'Вернуться в полноэкранный режим',
        'out': 'Вы вышли из полноэкранного режима', 'exits_left': 'Осталось выходов:',
        'kicked': 'Вы выбиты из игры: слишком много выходов из полного экрана. Ваши клетки остались команде.',
        'no_fs': 'На этом устройстве нет полноэкранного режима. Откройте игру на компьютере в Chrome или Edge.',
        'what_prints': 'Что выведет эта программа?', 'write_code': 'Напишите программу', 'answer': 'Ответ',
        'check': 'Проверить', 'cancel': 'Отмена', 'checking': 'Проверяем…', 'input': 'Пример ввода', 'output': 'Пример вывода',
        'wrong': 'Неверно. Пауза', 'sec': 'сек', 'tests': 'Пройдено тестов', 'captured': 'Клетка захвачена!',
        'cooldown': 'Пауза после ошибки', 'enemy': 'Чужая клетка — задача сложнее', 'points': 'очк.',
        'over': 'Игра окончена', 'place': 'место', 'your_team': 'Ваша команда', 'you': 'Вы', 'coins': 'Монеты за игру',
        'unavailable': 'Проверка сейчас недоступна, попробуйте ещё раз.', 'noquestion': 'Не получилось подобрать задачу, попробуйте другую клетку.',
        'far': 'Можно шагнуть только на соседнюю клетку суши.', 'me_btn': 'К моей точке', 'score': 'Очки',
        'grass': 'Трава', 'forest': 'Лес', 'fort': 'Крепость', 'own': 'Своя клетка — проход свободный',
        'help': 'Нажмите на соседнюю клетку (или стрелки), решите задачу — клетка станет вашей.',
        'enter_code': 'Введите код решения', 'time_up': 'Время вышло', 'results': 'Итоги',
    }
    if lang != 'kk':
        return ru
    return {k: UI_TRANSLATIONS.get(v, v) for k, v in ru.items()}
