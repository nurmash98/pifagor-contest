# -*- coding: utf-8 -*-
"""3D-коворкинг класса: учитель открывает зал на урок и выбирает задачи, ученики садятся за столы и решают."""
import json
import time
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from . import hints as hints_mod
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import room as rm
from .models import Student, StudyRoom, Task

COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6', '#3b82f6', '#6366f1', '#a855f7', '#ec4899', '#0ea5e9', '#84cc16', '#f43f5e']


def _lang(request):
    return 'kk' if request.session.get('content_lang') == 'kk' else 'ru'


def open_room_for_class(cls):
    """Открытый зал класса (кеш 30 секунд — для полосы-приглашения на каждой странице)."""
    if not cls:
        return None
    key = f'room:open:{cls}'
    rid = cache.get(key)
    if rid is None:
        now = timezone.now()
        r = (StudyRoom.objects.filter(school_class=cls, closed_at__isnull=True)
             .filter(ends_at__isnull=True).first() or
             StudyRoom.objects.filter(school_class=cls, closed_at__isnull=True, ends_at__gt=now).first())
        rid = r.id if r else 0
        cache.set(key, rid, 30)
    return rid or None


def _forget_open(cls):
    cache.delete(f'room:open:{cls}')


def _can_teach(user, room):
    if user.is_superuser or room.owner_id == user.id:
        return True
    from .views import _managed_classes
    scope = _managed_classes(user)
    return scope is None or room.school_class in scope


def _access(request, room_id, cached=False):
    """(room, student, teacher?) или Http404.
    cached=True — для частого опроса (каждые 3 сек): зал и ученик берутся из кеша, база не трогается."""
    if cached:
        room = cache.get(f'room:obj:{room_id}')
        if room is None:
            room = get_object_or_404(StudyRoom, id=room_id)
            room.n_tasks = room.tasks.count()
            cache.set(f'room:obj:{room_id}', room, 15)
    else:
        room = get_object_or_404(StudyRoom, id=room_id)
    if request.user.is_staff:
        if not _can_teach(request.user, room):
            raise Http404
        return room, None, True
    key = f'room:st:{request.user.id}'
    st = cache.get(key) if cached else None
    if st is None:
        st = Student.objects.filter(user=request.user).first()
        if cached and st is not None:
            cache.set(key, st, 120)
    if st is None or st.school_class != room.school_class:
        raise Http404
    return room, st, False


def _label(st):
    """Плашка над головой: имя на своём баннере, как на сайте (аватар — на голове персонажа)."""
    from django.template.loader import render_to_string
    from . import avatars
    html = render_to_string('room_label.html', {'st': st})
    pic = avatars.get(st.avatar)
    img = avatars.img_url(pic) if pic else (avatars.custom_url(st.avatar) or '')
    return {'h': ' '.join(html.split()), 'ai': img}


def _base_lazy(request, st, teacher):
    return lambda: _base(request, st, teacher)


def _base(request, st, teacher):
    if not teacher:
        return dict(_base_plain(request, st, teacher), **_label(st))
    return _base_plain(request, st, teacher)


def _base_plain(request, st, teacher):
    if teacher:
        name = request.user.get_full_name() or request.user.username
        base = {'n': name, 'teacher': True, 'c': -1, 'x': rm.TEACHER_SPOT[0], 'y': rm.TEACHER_SPOT[1], 'seat': 't'}   # руководитель — в своём кабинете
        return dict(base, **_teacher_look(request))
    return {'n': st.full_name or request.user.username, 'teacher': False, 'c': st.id % len(COLORS),
            'x': 9 + request.user.id % 10, 'y': rm.SPAWN_Y}                              # входят в коридор офиса


def _teacher_look(request):
    """Наш учитель — как в офисе выходного дня: имя «… ағай» и его аватар."""
    from django.templatetags.static import static
    from . import office as of
    ag = of.agai_by_username(request.user.username)
    return {'n': ag['name'], 'ai': static(ag['av'])} if ag else {}



# ------------------------------------------------------------------ учитель

@staff_member_required
def room_manage(request):
    from .codegame_views import _my_classes
    from .scratch_levels import is_junior_class
    classes = [(c, n) for c, n in _my_classes(request.user) if not is_junior_class(c)]
    mine = {c for c, _ in classes}
    if request.method == 'POST':
        cls = request.POST.get('school_class')
        if cls not in mine:
            messages.error(request, 'Выберите свой класс.')
            return redirect('room_manage')
        from .models import Subtopic, Topic
        # несколько тем / подтем курса (галочки); старые формы присылают одну topic / subtopic
        sub_ids = [int(x) for x in request.POST.getlist('subtopics') + [request.POST.get('subtopic') or ''] if str(x).isdigit()]
        top_ids = [int(x) for x in request.POST.getlist('topics') + [request.POST.get('topic') or ''] if str(x).isdigit()]
        subs = list(Subtopic.objects.select_related('topic').filter(id__in=sub_ids).order_by('topic__order', 'order', 'id'))
        if request.POST.get('subtopic') and not request.POST.getlist('topics'):
            top_ids = [i for i in top_ids if i not in {x.topic_id for x in subs}]   # одна подтема = только она, не вся тема
        tops = list(Topic.objects.filter(id__in=top_ids).order_by('order', 'id'))
        sub = subs[0] if len(subs) == 1 and not tops else None
        topic = sub.topic if sub else (tops[0] if tops else (subs[0].topic if subs else None))
        ids = [int(x) for x in request.POST.getlist('tasks') if x.isdigit()]
        if ids:                                            # старый способ: задачи отмечены вручную
            tasks = list(Task.objects.filter(id__in=ids, hidden=False))
            by_level = True
        else:                                              # задачи выбранных подтем и тем курса
            seen, tasks = set(), []
            # порядок как в курсе: сначала вся первая тема (A → B → C), потом вторая и т.д.
            srcs = []
            for tp in tops:                                # «вся тема» = её задачи + задачи всех подтем
                srcs.append(((tp.course_id, tp.order, tp.id, -1, 0), tp))
                srcs += [((tp.course_id, tp.order, tp.id, x.order, x.id), x) for x in tp.subtopics.all()]
            srcs += [((x.topic.course_id, x.topic.order, x.topic_id, x.order, x.id), x) for x in subs]
            srcs.sort(key=lambda p: p[0])
            by_topic = []                                  # [(ключ темы, [задачи])]
            for key, src in srcs:
                if not by_topic or by_topic[-1][0] != key[:3]:
                    by_topic.append((key[:3], []))
                for t in src.tasks.filter(hidden=False):
                    if t.id not in seen:
                        seen.add(t.id); by_topic[-1][1].append(t)
            for _, group in by_topic:
                tasks += sorted(group, key=lambda t: (t.level, t.id))
            by_level = False
        if not tasks:
            messages.error(request, 'Выберите хотя бы одну задачу на урок.')
            return redirect('room_manage')
        try:
            minutes = max(5, min(240, int(request.POST.get('minutes') or 45)))
        except ValueError:
            minutes = 45
        now = timezone.now()
        StudyRoom.objects.filter(school_class=cls, closed_at__isnull=True).update(closed_at=now)   # один зал на класс
        for prev in StudyRoom.objects.filter(school_class=cls, top_awarded=False, closed_at__isnull=False)[:5]:
            rm.award_top(prev)
        if topic is None:
            topic = Topic.objects.filter(id=request.POST.get('theory_topic') or 0).first()
        names = [x.name for x in subs if x.topic_id not in {t.id for t in tops}] + [t.name for t in tops]
        title = (request.POST.get('title') or '').strip()[:120] or ' + '.join(names) or (topic.name if topic else '')
        room = StudyRoom.objects.create(school_class=cls, owner=request.user, title=title[:120],
                                        ends_at=now + timedelta(minutes=minutes), theory_topic=topic, theory_subtopic=sub,
                                        theory_text=(request.POST.get('theory_text') or '').strip()[:20000])
        room.tasks.set(tasks)
        valid = {t.id for t in tasks}
        order = []
        for x in (request.POST.get('order') or '').split(','):
            if x.strip().isdigit() and int(x) in valid and int(x) not in order:
                order.append(int(x))
        room.task_order = order + [t.id for t in (sorted(tasks, key=lambda t: (t.level, t.id)) if by_level else tasks) if t.id not in order]
        room.save(update_fields=['task_order'])
        _forget_open(cls)
        return redirect('room_play', room_id=room.id)
    now = timezone.now()
    rooms = list(StudyRoom.objects.filter(school_class__in=mine).prefetch_related('tasks')[:30])
    for r in rooms:
        r.open_now = r.is_open(now)
        r.online = len(rm.people(r.id)) if r.open_now else 0
        if not r.open_now and not r.top_awarded:
            rm.award_top(r)
    tasks = list(Task.objects.filter(hidden=False).prefetch_related('tags').order_by('level', 'title'))
    from collections import Counter
    from .models import Topic
    from .models import SPECIAL_TAGS
    tag_count = Counter(g for t in tasks for g in t.tags.all() if g.name not in SPECIAL_TAGS)
    tags = sorted(tag_count.items(), key=lambda kv: kv[0].name.lower())
    topics = []
    for tp in Topic.objects.select_related('course').prefetch_related('tasks', 'subtopics').order_by('course__name', 'order', 'name'):
        has = bool(tp.theory_html or tp.theory_video_url or any(st.theory_html for st in tp.subtopics.all()))
        if has:
            topics.append({'id': tp.id, 'title': f'{tp.course.name} — {tp.name}', 'tasks': [t.id for t in tp.tasks.all()]})
    from .models import Course
    tree = []                                          # курс → темы → подтемы (только с задачами)
    for c in Course.objects.select_related('created_by').prefetch_related('topics__tasks', 'topics__subtopics__tasks').order_by('name'):
        tps = []
        for tp in c.topics.all():
            subs = [{'id': st.id, 'name': st.name, 'n': sum(1 for t in st.tasks.all() if not t.hidden),
                     'lv': ''.join(sorted({t.level for t in st.tasks.all() if not t.hidden}))} for st in tp.subtopics.all()]
            subs = [x for x in subs if x['n']]
            n = sum(1 for t in tp.tasks.all() if not t.hidden)
            if subs or n:
                tps.append({'id': tp.id, 'name': tp.name, 'n': n, 'subs': subs})
        if tps:
            mine_c = bool(c.created_by_id) and c.created_by.user_id == request.user.id
            tree.append({'id': c.id, 'name': ('⭐ ' if mine_c else '') + c.name, 'topics': tps, 'mine': mine_c})
    tree.sort(key=lambda x: not x['mine'])                 # свои курсы учителя — первыми и выбраны по умолчанию
    return render(request, 'room_manage.html', {'classes': classes, 'rooms': rooms, 'tasks': tasks, 'tags': tags,
                                                'topics': topics, 'topics_json': json.dumps({t['id']: t['tasks'] for t in topics}),
                                                'tree_json': json.dumps(tree, ensure_ascii=False)})


@staff_member_required
@require_POST
def room_close(request, room_id):
    room = get_object_or_404(StudyRoom, id=room_id)
    if not _can_teach(request.user, room):
        raise Http404
    if room.closed_at is None:
        room.closed_at = timezone.now()
        room.save(update_fields=['closed_at'])
    rm.award_top(room)
    _forget_open(room.school_class)
    cache.delete(f'room:obj:{room.id}')
    messages.success(request, f'Рабочий день в компании ({room.school_class}) закончен.')
    return redirect('room_manage')


# ------------------------------------------------------------------ зал

@login_required
def room_enter(request):
    """Ученик: войти в открытый зал своего класса."""
    st = Student.objects.filter(user=request.user).first()
    rid = open_room_for_class(st.school_class if st else None)
    if request.user.is_staff:
        return redirect('room_manage')
    if not rid:
        messages.info(request, 'Учитель пока не открыл коворкинг для вашего класса.')
        return redirect('codegames')
    return redirect('room_play', room_id=rid)


def _theory_items(room):
    """Теория урока для боковой панели: [{'title', 'url', 'kind'}]."""
    out = []
    if room.theory_text:
        out.append({'title': 'Заметки учителя', 'url': reverse('room_theory', args=[room.id, 'text', 0]), 'kind': 'text'})
    tp = room.theory_topic
    sub = room.theory_subtopic
    if sub is not None:
        if sub.theory_html:
            out.append({'title': sub.name, 'url': reverse('room_theory', args=[room.id, 'sub', sub.id]), 'kind': 'html'})
        if tp is not None and tp.theory_html:
            out.append({'title': tp.name, 'url': reverse('room_theory', args=[room.id, 'topic', tp.id]), 'kind': 'html'})
    elif tp is not None:
        if tp.theory_html:
            out.append({'title': tp.name, 'url': reverse('room_theory', args=[room.id, 'topic', tp.id]), 'kind': 'html'})
        for st in tp.subtopics.all():
            if st.theory_html:
                out.append({'title': st.name, 'url': reverse('room_theory', args=[room.id, 'sub', st.id]), 'kind': 'html'})
        if tp.youtube_embed_url():
            out.append({'title': '▶ ' + tp.name, 'url': tp.youtube_embed_url(), 'kind': 'video'})
    return out


@login_required
def room_theory(request, room_id, kind, item_id):
    """Теория урока в зале: заметки учителя или HTML-теория темы курса (в песочнице, как на странице курса)."""
    from django.http import HttpResponse
    room, st, teacher = _access(request, room_id)
    tp = room.theory_topic
    if kind == 'text' and room.theory_text:
        return render(request, 'room_theory_text.html', {'room': room})
    html = ''
    if kind == 'topic' and tp is not None and tp.id == item_id:
        html = tp.theory_html
    elif kind == 'sub' and tp is not None:
        sub = tp.subtopics.filter(id=item_id).first()
        html = sub.theory_html if sub else ''
    if not html:
        raise Http404
    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    response['Content-Security-Policy'] = 'sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox'
    return response


ROOM_TEXTS = {
    'sit_first': 'Сначала сядьте за стол', 'taken': 'Это место занято', 'wait': 'Не так быстро — подождите пару секунд',
    'teacher_seat': 'Это стол учителя', 'you': 'вы', 'hand_up': 'Опустить руку', 'hand': 'Поднять руку', 'lower': 'опустить',
    'none': '—', 'of': 'из', 'at_desk': 'Вы за столом — откройте задание справа', 'close_q': 'Закончить рабочий день для всего класса?',
    'fs_title': 'Компания «Пифагор»', 'fs_start': 'Войти в полноэкранном режиме', 'fs_out': 'Вы вышли из полноэкранного режима',
    'fs_back': 'Вернуться в полноэкранный режим', 'exits': 'Выходов из полного экрана / на другие вкладки',
    'notes': 'Объяснение учителя',
    'far': 'Подойдите к столу руководителя', 'busy': 'Сначала закончите текущее задание', 'all_done': 'Все задания выполнены — отличная работа!',
    'got': 'Ассистент дал задание', 'go_boss': 'Подойдите к ассистенту (🧑‍💼 в коридоре) за заданием', 'cur': 'Текущее задание',
    'in_work': 'в работе', 'done': 'выполнено', 'salary': 'Зарплата', 'to_boss': 'К руководителю', 'paid': 'Задание выполнено! Зарплата',
    'boss': 'Руководитель', 'tenge': '₸', 'no_task': 'нет задания',
    'swap': 'Поменять задачу',     'swap_limit': 'Поменять задачу можно не больше 2 раз за урок', 'no_other': 'Других задач нет — решите эту',
    'swapped': 'Новое задание',
    'fine': 'Штраф', 'fine_q': 'Штраф для {name}: сколько тенге вычесть из зарплаты?', 'fine_why': 'За что? (можно оставить пустым)',
    'fined': 'Руководитель выписал штраф',
    'stress': 'Стресс', 'stress_hi': 'Стресс зашкаливает — клавиши путаются! Выпейте кофе ☕ или посидите с котиками 🐈',
    'coffee_ok': 'Кофе! Стресс −40', 'coffee_wait': 'Кофемашина ещё готовит — через {s} с',
    'cats_pet': 'Котики мурлычут — стресс уходит', 'ev_quake': 'Землетрясение! Держитесь за столы',
    'ev_storm': 'Гроза! В офисе мигает свет', 'ev_snow': 'Метель! Все ходят медленнее',
    'ev_cats': 'Радужные котики прилетели — стресс не растёт!', 'rain': 'Дождь из монет! Ходите по монетам и ловите',
    'rain_got': 'Поймал монету', 'rain_taken': 'Эту монету уже забрали', 'swap_go': 'Идите к ассистенту — он даст новое задание', 'chat': 'Чат', 'chat_empty': 'Сообщений пока нет. Нажмите на человека в офисе, чтобы написать ему.',
    'earned': 'Заработано сегодня', 'coins_x2': 'монет (x2)', 'sit_own': 'Сядьте за стол в кабинете своей параллели',
    'other_grade': 'Это кабинет другой параллели — садиться можно только в своём',
    'admin_only': 'Это администрация — здесь сидят учителя. Можно зайти и задать вопрос',
    'admin_hint': 'Вы в администрации. Топ какой параллели показать:',
    'dm_hint': 'Напишите вопрос — его увидит только этот человек', 'dm_new': 'новое сообщение (нажмите на него)',
    'asst_role': 'ассистент',
    'asst_hi': 'Здравствуйте! Я {name}, ассистент руководителя. Выдаю задания и помогаю с ними.',
    'asst_take': 'Получить задание',
    'asst_hint': 'Подсказка',
    'asst_theory': 'Теория',
    'asst_open': 'Открыть задание',
    'asst_got': 'Держите задание: «{t}» (уровень {l}, зарплата {m}). Сядьте за свободный стол и откройте его справа.',
    'asst_busy': 'Сейчас у вас задание «{t}». Решите его — потом дам следующее. Нужна подсказка?',
    'asst_tip': 'Совет по теме:',
    'asst_ex': 'Разберите пример из условия: ввод {i} → вывод {o}. Проверьте свой код на этом примере.',
    'asst_fail': 'Ваше последнее решение: пройдено {p} из {n} тестов. Не проходит тест №{k}',
    'asst_fail_in': 'ввод:', 'ex_in': 'Ввод', 'ex_out': 'Вывод',
    'asst_ok': 'Ваше последнее решение прошло {p} из {n} тестов.',
    'asst_nosub': 'Вы ещё не отправляли решение. Отправьте — и я подскажу, на каком тесте ошибка.',
    'asst_more': 'Ещё подсказка',
    'asst_th_none': 'К этому уроку теории нет — спросите руководителя.',
    'h_btn': 'Подсказка', 'h_l1': 'Тема и пример', 'h_l2': 'Где ошибка', 'h_l3': 'Разбор кода', 'h_l4': 'Спросить ИИ',
    'h_free': 'бесплатно', 'h_yes': 'Да, показать',
    'h_confirm': 'Эта подсказка снизит зарплату за задачу на {p}%. Снимается, только если подсказка что-то нашла. Показать?',
    'h_pay_now': 'Зарплата за эту задачу сейчас:',
    'h_issues': 'Что я заметил в вашем коде:',
    'h_no_issues': 'Типичных ошибок в коде не нашёл — зарплата не снята. Посмотрите «Где ошибка» или спросите ИИ / руководителя.',
    'h_ai': 'Подсказка ИИ:', 'h_ai_nocode': 'Сначала напишите код — ИИ посмотрит его. Зарплата не снята.',
    'h_ai_limit': 'По этой задаче ИИ-подсказки на сегодня закончились (3 в день).',
    'h_ai_fail': 'ИИ сейчас не отвечает — зарплата не снята. Попробуйте чуть позже.',
    'h_ai_off': 'ИИ-подсказки пока не подключены.',
    'h_yours': 'Ваш вывод', 'h_expected': 'Ожидалось', 'h_wait': 'Думаю…', 'h_open_first': 'Сначала откройте задачу.',
    'asst_go': 'Иду к ассистенту…',
    'go_asst': 'Подойдите к ассистенту (🧑\u200d💼 в коридоре) — он даст задание',
    'to_asst': 'К ассистенту',
    'boss_chill': 'чилит 😎',
    'co_earned': 'Компания заработала сегодня',
    'company': 'Компания',
    'dm_gone': 'Этот человек уже вышел', 'dm_wait': 'Не так быстро', 'dm_empty': 'Напишите текст', 'pick_task': 'Вы за столом — выберите задачу справа',
}


def _texts(lang):
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    return {k: (UI_TRANSLATIONS.get(v, v) if lang == 'kk' else v) for k, v in ROOM_TEXTS.items()}


def _task_list(room, lang):
    """Задачи в том порядке, в каком руководитель их выдаёт."""
    order = {tid: i for i, tid in enumerate(room.task_order or [])}
    tasks = sorted(room.tasks.all(), key=lambda t: (order.get(t.id, 10 ** 6), t.level, t.id))
    return [{'id': t.id, 'title': t.get_display_title(lang), 'level': t.level, 'url': reverse('task_detail', args=[t.id]),
             'pay': rm.PAY.get(t.level, rm.PAY['A'])} for t in tasks]


@login_required
@require_POST
def room_lang(request, room_id):
    """RU/KZ прямо в зале, без перезагрузки (иначе сбросился бы полный экран)."""
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    room, st, teacher = _access(request, room_id)
    lang = request.POST.get('lang')
    if lang not in ('ru', 'kk'):
        return JsonResponse({'ok': False})
    request.session['content_lang'] = lang
    try:
        keys = [k for k in json.loads(request.POST.get('keys') or '[]') if isinstance(k, str)][:200]
    except ValueError:
        keys = []
    return JsonResponse({'ok': True, 'txt': _texts(lang), 'tasks': _task_list(room, lang),
                         'tr': {k: (UI_TRANSLATIONS.get(k, k) if lang == 'kk' else k) for k in keys}})


@login_required
def room_play(request, room_id):
    room, st, teacher = _access(request, room_id)
    if not room.is_open() and not teacher:
        messages.info(request, 'Рабочий день в компании уже закончился.')
        return redirect('codegames')
    lang = _lang(request)
    n = Student.objects.filter(school_class=room.school_class).count()
    tasks = _task_list(room, lang)
    me = request.user.id
    rm.touch(room.id, me, _base_lazy(request, st, teacher))
    if st is not None:
        rm.touch(room.id, me, {}, **_label(st))          # баннер/аватар могли смениться — обновляем при входе
    elif teacher and _teacher_look(request):
        rm.touch(room.id, me, {}, **_teacher_look(request))
    return render(request, 'room_play.html', {
        'room': room, 'teacher': teacher, 'me': me, 'office': False, 'co_name': room.school_class,
        'state_url': reverse('room_state', args=[room.id]), 'act_url': reverse('room_act', args=[room.id]),
        'lang_url': reverse('room_lang', args=[room.id]),
        'layout': json.dumps(rm.layout(n)), 'tasks_json': json.dumps(tasks, ensure_ascii=False),
        'hint_max': hints_mod.max_level(),
        'colors': json.dumps(COLORS), 'say_max': rm.SAY_MAX, 'theory': json.dumps(_theory_items(room), ensure_ascii=False), 'texts': json.dumps(dict(_texts(lang), fs_title=f'{_texts(lang)["company"]} «{room.school_class}»'), ensure_ascii=False),
    })


def _stats_rows(room, co, online, exits=None):
    """[[uid, имя, зарплата, выполнено заданий, в офисе, выходов, текущее задание (id|None)]] — по зарплате."""
    rows = []
    exits = exits or {}
    key = f'room:class:{room.school_class}'
    students = cache.get(key)
    if students is None:
        students = list(Student.objects.filter(school_class=room.school_class).values_list('id', 'full_name', 'user_id'))
        cache.set(key, students, 60)
    for sid, full_name, user_id in students:
        s = Student(id=sid, full_name=full_name, user_id=user_id)
        d = co.get(s.id, {})
        rows.append([s.user_id, s.full_name, d.get('salary', 0), len(d.get('done', [])), s.user_id in online,
                     exits.get(s.user_id, 0), d.get('cur')])
    rows.sort(key=lambda r: (-r[2], -r[3], not r[4], r[1]))
    return rows


@login_required
def room_state(request, room_id):
    room, st, teacher = _access(request, room_id, cached=True)
    me = request.user.id
    fresh = request.GET.get('fresh') == '1' and cache.add(f'room:fresh:{me}', 1, 3)
    if room.is_open():
        rm.touch(room.id, me, _base_lazy(request, st, teacher))
    ppl = rm.public(room.id)
    co = rm.company(room, fresh=fresh)
    online = {p[0] for p in ppl}
    if not room.is_open() and not room.top_awarded:      # время вышло — награды топ-3 (один раз)
        rm.award_top(StudyRoom.objects.get(pk=room.pk))
        cache.delete(f'room:obj:{room.id}')
    out = {'open': room.is_open(), 'people': ppl,
           'left': max(0, int((room.ends_at - timezone.now()).total_seconds())) if room.ends_at else None,
           'stats': _stats_rows(room, co, online, rm.exits(room.id) if teacher else None), 'n_tasks': getattr(room, 'n_tasks', None) or room.tasks.count(),
           'total': sum(d.get('salary', 0) for d in co.values())}
    if st is not None:
        mine = co.get(st.id, {})
        out['me'] = {'cur': mine.get('cur'), 'done': mine.get('done', []), 'salary': mine.get('salary', 0),
                     'fines': mine.get('fines', 0), 'fine_last': mine.get('fine_last')}
    if teacher:
        out['log'] = rm.log(room.id)[-30:]
    if room.ends_at and out['left'] is not None and out['left'] <= rm.RAIN_WINDOW + 2:
        out['rain'] = rm.rain_taken(room.id)
    _dm_state(request, room.id, out)
    return JsonResponse(out)


def _dm_state(request, rid, out):
    """Непрочитанные личные сообщения и открытая переписка (?dm=<uid>)."""
    me = request.user.id
    other = request.GET.get('dm')
    if other and other.isdigit():
        out['conv'] = [int(other), rm.dm_get(rid, me, other)]
    out['unread'] = rm.dm_unread(rid, me)
    out['now'] = round(time.time(), 1)                      # часы сервера — события в офисе у всех одновременно
    ids = [int(k) for k in out['unread'] if str(k).lstrip('-').isdigit() and int(k) > 0]
    if ids:                                                 # имена тех, кто написал (даже если уже ушёл)
        key = 'room:names:' + ','.join(map(str, sorted(ids)))
        names = cache.get(key)
        if names is None:
            names = {str(s.user_id): s.full_name for s in Student.objects.filter(user_id__in=ids).only('user_id', 'full_name')}
            for u in User.objects.filter(id__in=[i for i in ids if str(i) not in names]).only('id', 'first_name', 'last_name', 'username'):
                names[str(u.id)] = (u.get_full_name() or u.username)
            cache.set(key, names, 300)
        out['names'] = names


@login_required
@require_POST
def room_act(request, room_id):
    room, st, teacher = _access(request, room_id)
    if not room.is_open():
        return JsonResponse({'error': 'closed'})
    me = request.user.id
    base = _base_lazy(request, st, teacher)
    act = request.POST.get('a')
    if act == 'move':
        try:
            x = max(0.0, min(60.0, float(request.POST.get('x'))))
            y = max(0.0, min(60.0, float(request.POST.get('y'))))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        seat = (request.POST.get('seat') or '')[:4] or None
        if seat == 't' and not teacher:
            return JsonResponse({'error': 'teacher_seat'})
        if seat and rm.seat_taken(room.id, seat, me):
            return JsonResponse({'error': 'taken'})
        rm.touch(room.id, me, base, x=round(x, 2), y=round(y, 2), seat=seat)
        return JsonResponse({'ok': True})
    if act == 'dm':
        rm.touch(room.id, me, base)
        err = rm.dm_send(room.id, me, request.POST.get('to') or 0, request.POST.get('text'))
        return JsonResponse({'error': err} if err else {'ok': True, 'conv': rm.dm_get(room.id, me, request.POST.get('to'))})
    if act == 'say':
        rm.touch(room.id, me, base)
        err = rm.say(room.id, me, request.POST.get('text'))
        return JsonResponse({'error': err} if err else {'ok': True})
    if act == 'hand':
        p = rm.touch(room.id, me, base)
        rm.touch(room.id, me, base, hand=not p.get('hand'))
        return JsonResponse({'ok': True})
    if act == 'take' and not teacher:
        task, err = rm.take(room, st, rm.get(room.id, me))
        if err:
            return JsonResponse({'error': err})
        rm.log_event(room.id, st.full_name, f'📋 взял задание «{task.title}» ({task.level})')
        return JsonResponse({'ok': True, 'task': task.id})
    if act == 'coin' and st is not None:                   # дождь из монет в последнюю минуту урока
        from .models import CoinTransaction
        try:
            n = int(request.POST.get('n'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        left = (room.ends_at - timezone.now()).total_seconds() if room.ends_at else 1e9
        since = rm.RAIN_WINDOW - left                       # сколько секунд идёт дождь
        if not (0 <= n < rm.RAIN_COINS) or since < -3 or left < -3 or n * rm.RAIN_WINDOW / rm.RAIN_COINS > since + 3:
            return JsonResponse({'error': 'bad'})
        if not cache.add(f'room:{room.id}:rain:{n}', me, 3600):
            return JsonResponse({'error': 'taken'})
        CoinTransaction.objects.get_or_create(student=st, key=f'rain:{room.id}:{n}',
                                              defaults=dict(kind='game', amount=1, note='Дождь из монет', at=timezone.now()))
        return JsonResponse({'ok': True})
    if act == 'swap' and not teacher:
        old_cur = rm.company(room, fresh=True).get(st.id, {}).get('cur')
        task, err = rm.swap(room, st)
        if err:
            return JsonResponse({'error': err})
        old_t = room.tasks.filter(id=old_cur).first()
        rm.log_event(room.id, st.full_name, f'🔄 поменял задание «{old_t.title if old_t else "?"}» → «{task.title}» ({task.level})')
        return JsonResponse({'ok': True, 'task': task.id})
    if act == 'hint' and not teacher:
        from .hints import build
        from .models import RoomAssignment
        asg = None
        if (request.POST.get('task') or '').isdigit():   # задача, открытая справа (текущая или уже сданная)
            asg = RoomAssignment.objects.filter(room=room, student=st, task_id=int(request.POST['task']), skipped_at__isnull=True).first()
        if asg is None:
            cur = rm.company(room).get(st.id, {}).get('cur')
            asg = RoomAssignment.objects.filter(room=room, student=st, task_id=cur, skipped_at__isnull=True).first() if cur else None
        if asg is None:
            return JsonResponse({'error': 'no_task'})
        task = room.tasks.filter(id=asg.task_id).prefetch_related('tags').first()
        if task is None:
            return JsonResponse({'error': 'no_task'})
        d = build(task, st, request.POST.get('level'), f'room:{room.id}', asg.pay, _lang(request))
        if d.get('paid') and int(d['level']) in d['paid']:
            cache.delete(f'room:{room.id}:co')          # зарплата пересчитается с учётом подсказки
        return JsonResponse(d)
    if act == 'exit' and not teacher:
        rm.add_exit(room.id, me)
        return JsonResponse({'ok': True})
    if act == 'fine' and teacher:
        from .models import RoomFine
        try:
            uid, amount = int(request.POST.get('uid')), int(request.POST.get('amount'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        if not 0 < amount <= 1000000:
            return JsonResponse({'error': 'bad'})
        who = Student.objects.filter(user_id=uid, school_class=room.school_class).first()
        if who is None:
            return JsonResponse({'error': 'bad'})
        reason = ' '.join((request.POST.get('reason') or '').split())[:100]
        RoomFine.objects.create(room=room, student=who, amount=amount, reason=reason, by=request.user)
        cache.delete(f'room:{room.id}:co')
        rm.log_event(room.id, who.full_name, f'💸 штраф −{amount:,} ₸'.replace(',', ' ') + (f' ({reason})' if reason else ''))
        return JsonResponse({'ok': True})
    if act == 'lower' and teacher:
        try:
            uid = int(request.POST.get('uid'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        p = rm.get(room.id, uid)
        if p:
            rm.touch(room.id, uid, p, hand=False)
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'bad'})
