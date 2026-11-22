# -*- coding: utf-8 -*-
"""«Офис выходного дня» (сб/вс 9:00–20:00, без учителя): страница, опрос, действия, язык."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import office as of
from . import room as rm
from .models import Student, Task
from .room_views import COLORS, _label, _lang, _texts


def _who(request):
    """(student, grade, teacher?) — ученики 5–11 классов и учителя (смотрят)."""
    if request.user.is_staff:
        return None, None, True
    key = f'office:who:{request.user.id}'
    got = cache.get(key)
    if got is None:
        from .quarters import grade_of
        st = Student.objects.filter(user=request.user).first()
        g = grade_of(st.school_class) if st else ''
        got = (st, g if g.isdigit() and int(g) >= 5 else None)
        cache.set(key, got, 300)
    return got[0], got[1], False


def _base(request, st, grade, teacher):
    def make():
        if teacher:
            ag = of.agai_by_username(request.user.username)          # наш преподаватель — заходит «собой»-ағаем
            extra = {'n': ag['name'], 'ai': static(ag['av']), 'ag': ag['key']} if ag else {}
            return dict({'n': request.user.get_full_name() or request.user.username, 'teacher': True, 'c': -1, 'g': of.ADMIN,
                         'x': of.SPAWN[0], 'y': of.SPAWN[1], 'j': timezone.now().timestamp()}, **extra)
        return dict({'n': st.full_name, 'teacher': False, 'c': st.id % len(COLORS), 'sid': st.id, 'g': grade,
                     'x': of.SPAWN[0] + request.user.id % 8, 'y': of.SPAWN[1], 'j': timezone.now().timestamp()}, **_label(st))
    return make


def _office_texts(lang):
    """Тексты зала + свои для офиса (заставка полного экрана без «урока» и «учителя»)."""
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    t = _texts(lang)
    tr = (lambda v: UI_TRANSLATIONS.get(v, v)) if lang == 'kk' else (lambda v: v)
    t['fs_title'] = tr('Офис выходного дня')
    t['gate_text'] = tr('Офис работает в полноэкранном режиме — так удобнее решать задачи.')
    for k, v in BOT_TEXTS.items():
        t[k] = tr(v)
    return t


BOT_TEXTS = {
    'agai_not_mine': 'Я веду другие классы. Ваш преподаватель — {name}, подойдите к нему.',
    'agai_away': 'сейчас не в офисе',
    'agai_ask_hint': 'Задайте вопрос преподавателю — он ответит здесь. Если его нет в офисе, он увидит вопрос, когда зайдёт.',
    'agai_here': 'в офисе',

    'agai_role': 'преподаватель',
    'agai_act_watch': '👀 смотрит, как решают',
    'agai_act_walk': '🚶 обходит офис',
    'agai_act_tea': '☕ пьёт чай',
    'agai_act_talk': '💬 беседует с коллегами',
    'agai_tip1': 'Не спешите отправлять — сначала проверьте пример из условия.',
    'agai_tip2': 'Прочитайте условие ещё раз: что именно нужно вывести?',
    'agai_tip3': 'Проверьте крайние случаи: 0, 1 и отрицательные числа.',
    'agai_tip4': 'range(a, b) не включает b — частая ошибка!',
    'agai_tip5': 'В Python важны отступы: тело цикла — 4 пробела.',
    'agai_tip6': 'Застряли? Пройдите код по шагам на листочке.',
    'agai_tip7': 'Молодцы, что пришли в выходной! 💪',
    'agai_tip8': 'Сначала решите задачу уровня A, потом берите B и C.',

    'earned_today': 'Офис заработал сегодня',

    'mgr_act_report': '📋 проверяет отчёты',
    'mgr_act_files': '🗂 ищет папку с делами',
    'mgr_act_watch': '👀 смотрит, как идёт работа',
    'mgr_act_phone': '📞 говорит по телефону',
    'mgr_act_coffee': '☕ пьёт кофе',
    'mgr_act_laptop': '💻 печатает отчёт',
    'mgr_coming': '👋 иду к вам',
    'mgr_say_phone': 'Алло! Да, команда работает 💪',
    'mgr_say_watch': 'Отлично идёте, так держать!',
    'mgr_say_coffee': 'Кофе — топливо программиста ☕',

    'bot_to_mgr': 'Задачи в офисе дают руководители: Сэм Альтман — 5–8 классы, Илон Маск — 9–11 классы. Подойдите к своему, выберите тему — он даст 3 задачи и премию 10 000 ₸ за все три.',
    'pick_task': 'Вы за столом — откройте задание руководителя справа',
    'mgr_role': 'руководитель {g} кл.',
    'mgr_going': 'Идёте к руководителю…',
    'mgr_hello': 'Здравствуйте! Я {name}, руководитель {g} классов. Какую тему вы сейчас проходите? Выберите ниже — дам 3 задачи там, где вы остановились.',
    'mgr_again': 'Отличная работа, премия ваша! Готовы к новому заданию? Выберите тему ниже.',
    'mgr_other': 'Я руководитель {g} классов. Ваш руководитель — {other}, он ходит у кабинетов своих классов.',
    'mgr_busy': 'У вас уже есть моё задание — осталось решить: {n}. Сначала закончите его, потом дам новое.',
    'mgr_gave': 'Вот ваше задание по теме «{tag}» (уровень {l}). Решите все три — получите премию 10 000 ₸!',
    'mgr_all': 'Вы решили все задачи по всем темам — я в восторге! 🏆',
    'pkg_title': 'Задание руководителя',
    'pkg_bonus': 'Решено {n} из 3 — за все три премия 10 000 ₸',
    'pkg_go': 'Подойдите к своему руководителю — он даст 3 задачи и премию 10 000 ₸ за их решение.',
    'pkg_more': 'Премия получена! Руководитель даст новое задание.',
    'pkg_paid': 'Задание руководителя выполнено! Премия +10 000 ₸',
    'far_manager': 'Подойдите ближе к руководителю',
    'pkg_busy': 'Сначала закончите текущее задание руководителя',
    'pkg_none': 'По этой теме вы всё решили — выберите другую',
    'pkg_bad': 'Тема не найдена',
    'bot_role': 'администратор',
    'bot1': 'Айна',
    'bot2': 'Арман',
    'bot_water': '💧 поливает цветы',
    'bot_coffee': '☕ варит кофе',
    'bot_water2': '🚰 меняет воду в кулере',
    'bot_walk': '👀 обходит офис',
    'bot_desk': '🛎 на ресепшене',
    'bot_talk': '💬 отвечает на вопрос',
    'bot_hello': 'Привет! Я администратор офиса. Нажми на меня, если нужна помощь 🙂',
    'bot_hi': 'Здравствуйте! Я {name}, администратор офиса. Чем помочь? Выберите вопрос ниже 👇',
    'botq_x2': 'Как получить монеты x2?',
    'botq_cab': 'Где мой кабинет?',
    'botq_top': 'Как попасть в топ-3?',
    'botq_earn': 'Сколько я заработал?',
    'botq_advice': 'Где взять задачи?',
    'botq_theory': 'Где теория по задаче?',
    'botq_rules': 'Как тут всё устроено?',
    'bot_a_x2': 'Садитесь за стол в кабинете своей параллели и решайте задачи прямо здесь. За задачу, которую вы впервые решили на 10/10 в офисе, — обычные монеты и столько же сверху: A — 2, B — 4, C — 6. Старые решения не считаются.',
    'bot_a_rules': 'Офис открыт по субботам и воскресеньям с 9:00 до 20:00. Садитесь в кабинете своей параллели, выбирайте тему и задачу справа. За решённые задачи — монеты x2 и зарплата: A — 10 000 ₸, B — 20 000 ₸, C — 30 000 ₸. В 20:00 топ-3 каждой параллели получают по 20 монет. Учителя сидят в «Администрации» — им можно написать, нажав на них.',
    'bot_a_earn': 'Сегодня вы заработали {m}, монет за офис: +{c}, решено задач: {n}.',
    'bot_a_top_in': 'Вы сейчас на {p} месте в своей параллели — так держать! В 20:00 топ-3 получают по 20 монет.',
    'bot_a_top_out': 'Вы на {p} месте. Решайте задачи посложнее — B и C дают больше зарплаты.',
    'bot_a_top_none': 'Вы пока не в списке — решите первую задачу, и вы появитесь в топе.',
    'bot_a_top_third': 'У третьего места сейчас {m}.',
    'bot_a_cab': 'Ваш кабинет — «{g} класс». Садиться можно только там. Могу отвести!',
    'bot_a_cab_t': 'Учителя сидят в кабинете «Администрация».',
    'bot_lead': 'Отведи меня',
    'bot_follow': 'Идёмте за мной! Садитесь за свободный стол 🙂',
    'bot_a_adv': 'Попробуйте «{t}» — уровень {l}, зарплата {m}.',
    'bot_a_adv_none': 'Вы решили все задачи — вот это да! 🏆',
    'bot_open': 'Открыть задачу',
    'bot_sit_then': 'Сначала сядьте за стол в своём кабинете — я провожу. Потом откройте задачу справа.',
    'bot_a_th_first': 'Сначала откройте задачу, а потом спросите меня — я найду теорию по ней.',
    'bot_a_th': 'Вот теория к задаче «{t}»:',
    'bot_a_th_none': 'К задаче «{t}» теории в курсах нет. Попробуйте раздел «Курсы» или спросите учителя в «Администрации».',
    'bot_teacher': 'Это для учеников 🙂 Вы — учитель, вы и так всё знаете!'}



def _tasks(lang):
    key = f'office:tasks:{lang}'
    out = cache.get(key)
    if out is None:
        out, tags = [], {}
        for t in Task.objects.filter(hidden=False).prefetch_related('tags').order_by('level', 'id'):
            ids = []
            for g in t.tags.all():
                tags[g.id] = g.name
                ids.append(g.id)
            out.append({'id': t.id, 'title': t.get_display_title(lang), 'level': t.level, 'url': reverse('task_detail', args=[t.id]),
                        'pay': rm.PAY.get(t.level, rm.PAY['A']), 'tags': ids})
        out = (out, sorted(tags.items(), key=lambda kv: kv[1].lower()))
        cache.set(key, out, 300)
    return out


@login_required
def office_play(request):
    st, grade, teacher = _who(request)
    if not teacher and grade is None:
        messages.info(request, 'Офис выходного дня — для учеников 5–11 классов.')
        return redirect('codegames')
    if not of.is_open() and not teacher:
        messages.info(request, 'Офис открыт по субботам и воскресеньям с 9:00 до 20:00.')
        return redirect('codegames')
    day = timezone.localdate()
    r = of.rid(day)
    rm.touch(r, request.user.id, _base(request, st, grade, teacher))
    if st is not None:
        rm.touch(r, request.user.id, {}, **_label(st))
    lang = _lang(request)
    tasks, tags = _tasks(lang)
    from .models import Submission
    solved = list(Submission.objects.filter(student=st, is_graded=True, score=10).values_list('task_id', flat=True)) if st else []
    room = type('R', (), {'id': 0, 'school_class': '', 'title': ''})()
    return render(request, 'room_play.html', {
        'room': room, 'teacher': teacher, 'me': request.user.id, 'office': True, 'my_grade': of.ADMIN if teacher else (grade or ''),
        'state_url': reverse('office_state'), 'act_url': reverse('office_act'), 'lang_url': reverse('office_lang'),
        'layout': json.dumps(of.layout(of.grades())), 'tasks_json': json.dumps(tasks, ensure_ascii=False),
        'tags_json': json.dumps(tags, ensure_ascii=False), 'solved_json': json.dumps(solved), 'grades_json': json.dumps(of.grades()),
        'colors': json.dumps(COLORS), 'say_max': rm.SAY_MAX, 'theory': '[]', 'texts': json.dumps(_office_texts(lang), ensure_ascii=False),
        'agai_json': json.dumps(of.agai_list(st.school_class if st else ''), ensure_ascii=False),
    })


def _class_names(grade):
    key = f'office:names:{grade}'
    names = cache.get(key)
    if names is None:
        from .quarters import grade_of
        names = {sid: (uid, n) for sid, uid, n, c in Student.objects.values_list('id', 'user_id', 'full_name', 'school_class')
                 if grade_of(c) == grade}
        cache.set(key, names, 300)
    return names


def _inbox(unread, online):
    """Непрочитанные от тех, кого сейчас нет в офисе: [[uid, имя, сколько]] — чтобы можно было открыть и ответить."""
    away = [int(u) for u in unread if int(u) not in online]
    if not away:
        return []
    from django.contrib.auth.models import User
    ag = {a['username'].lower(): a['name'] for a in of.AGAI}
    names = {s.user_id: s.full_name for s in Student.objects.filter(user_id__in=away)}
    for u in User.objects.filter(id__in=[i for i in away if i not in names]):
        names[u.id] = ag.get(u.username.lower()) or u.get_full_name() or u.username
    return [[u, names.get(u, '?'), unread[str(u)]] for u in away if u in names]


def _dm_allowed_absent(request, teacher, st, to):
    """Можно ли писать тому, кого сейчас нет: ученик — своему преподавателю-ағаю; ағай — любому ученику (ответ)."""
    try:
        to = int(to)
    except (TypeError, ValueError):
        return False
    lst = of.agai_list(st.school_class if st else '')
    if teacher:
        return of.agai_by_username(request.user.username) is not None and Student.objects.filter(user_id=to).exists()
    return any(a['uid'] == to and a['mine'] for a in lst)


@login_required
def office_state(request):
    st, grade, teacher = _who(request)
    day = timezone.localdate()
    r = of.rid(day)
    open_ = of.is_open()
    if open_ and (teacher or grade):
        rm.touch(r, request.user.id, _base(request, st, grade, teacher))
    ppl = rm.public(r)
    if open_:
        of.collect(day)
    data = of.stats(day)
    online = {p[0] for p in ppl}
    show = grade if not teacher else (request.GET.get('g') or (of.grades() or [''])[0])
    names = _class_names(show)
    rows = [[names[sid][0], names[sid][1], m, n, names[sid][0] in online, 0, None] for sid, m, n in data.get(show, []) if sid in names]
    by_grade = {g: sum(r[1] for r in rs) for g, rs in data.items()}          # заработано за день: по параллелям и всем офисом
    out = {'open': open_, 'people': ppl, 'stats': rows[:15], 'total': sum(by_grade.values()), 'by_grade': by_grade,
           'left': max(0, int((of.closes_at() - timezone.now()).total_seconds())) if open_ else None}
    from .room_views import _dm_state
    _dm_state(request, r, out)
    out['inbox'] = _inbox(out.get('unread') or {}, online)
    if st is not None:
        key = f'office:me:{day}:{st.id}'
        mine = None if request.GET.get('fresh') == '1' else cache.get(key)
        if mine is None:
            from .models import OfficePackage, OfficeSolve
            pk = OfficePackage.objects.filter(student=st, day=day).first()
            solved_pk = set()
            if pk is not None:
                from .models import Submission
                solved_pk = set(Submission.objects.filter(student=st, task_id__in=pk.tasks, is_graded=True, score=10)
                                .values_list('task_id', flat=True))
            mine = {'rows': list(OfficeSolve.objects.filter(student=st, day=day).values_list('task_id', 'money', 'coins')),
                    'bonus': sum(OfficePackage.objects.filter(student=st, day=day).values_list('bonus', flat=True)),
                    'pkg': {'id': pk.id, 'tasks': pk.tasks, 'tag': pk.tag_name, 'level': pk.level, 'done': [t for t in pk.tasks if t in solved_pk],
                            'finished': pk.done_at is not None, 'bonus': pk.bonus} if pk else None}
            cache.set(key, mine, 15)
        rows_ = mine['rows']
        out['ome'] = {'money': sum(m for _, m, _ in rows_) + mine['bonus'], 'coins': sum(c for _, _, c in rows_), 'today': [t for t, _, _ in rows_],
                      'solved': [t for t, _, _ in rows_], 'pkg': mine['pkg'], 'bonus': mine['bonus']}
    return JsonResponse(out)


@login_required
@require_POST
def office_act(request):
    st, grade, teacher = _who(request)
    if not of.is_open():
        return JsonResponse({'error': 'closed'})
    if not teacher and grade is None:
        return JsonResponse({'error': 'bad'})
    r = of.rid(timezone.localdate())
    me = request.user.id
    base = _base(request, st, grade, teacher)
    act = request.POST.get('a')
    if act == 'move':
        try:
            x = max(0.0, min(80.0, float(request.POST.get('x'))))
            y = max(0.0, min(40.0, float(request.POST.get('y'))))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'bad'})
        seat = (request.POST.get('seat') or '')[:8] or None
        if seat and of.seat_grade(seat) != (of.ADMIN if teacher else grade):
            return JsonResponse({'error': 'admin_only' if of.seat_grade(seat) == of.ADMIN else 'other_grade'})
        if seat and rm.seat_taken(r, seat, me):
            return JsonResponse({'error': 'taken'})
        rm.touch(r, me, base, x=round(x, 2), y=round(y, 2), seat=seat)
        return JsonResponse({'ok': True})
    if act == 'dm':
        rm.touch(r, me, base)
        to = request.POST.get('to') or 0
        err = rm.dm_send(r, me, to, request.POST.get('text'), absent_ok=_dm_allowed_absent(request, teacher, st, to))
        return JsonResponse({'error': err} if err else {'ok': True, 'conv': rm.dm_get(r, me, request.POST.get('to'))})
    if act == 'say':
        rm.touch(r, me, base)
        err = rm.say(r, me, request.POST.get('text'))
        return JsonResponse({'error': err} if err else {'ok': True})
    if act == 'hand':
        p = rm.touch(r, me, base)
        rm.touch(r, me, base, hand=not p.get('hand'))
        return JsonResponse({'ok': True})
    if act == 'package' and not teacher:
        if not of.near_manager(rm.get(r, me), grade):
            return JsonResponse({'error': 'far_manager'})
        pkg, err = of.give_package(st, grade, request.POST.get('tag'), timezone.localdate())
        if err:
            return JsonResponse({'error': 'pkg_' + err})
        cache.delete(f'office:me:{timezone.localdate()}:{st.id}')
        return JsonResponse({'ok': True, 'tasks': pkg.tasks, 'level': pkg.level, 'tag': pkg.tag_name})
    if act == 'exit':
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'bad'})


@login_required
@require_POST
def office_lang(request):
    from .templatetags.ui_i18n import UI_TRANSLATIONS
    lang = request.POST.get('lang')
    if lang not in ('ru', 'kk'):
        return JsonResponse({'ok': False})
    request.session['content_lang'] = lang
    try:
        keys = [k for k in json.loads(request.POST.get('keys') or '[]') if isinstance(k, str)][:200]
    except ValueError:
        keys = []
    tasks, _ = _tasks(lang)
    return JsonResponse({'ok': True, 'txt': _office_texts(lang), 'tasks': tasks,
                         'tr': {k: (UI_TRANSLATIONS.get(k, k) if lang == 'kk' else k) for k in keys}})


@login_required
def office_bot(request):
    """Администратор офиса (персонаж без ИИ): «Посоветуй задачу» и «Где теория?». Ответы — по данным сайта."""
    from .models import Submission, Topic
    from .views import _course_open_for
    st, grade, teacher = _who(request)
    q = request.GET.get('q')
    lang = _lang(request)
    if q == 'advice':
        if st is None:
            return JsonResponse({'task': None})
        solved = set(Submission.objects.filter(student=st, is_graded=True, score=10).values_list('task_id', flat=True))
        tasks, _ = _tasks(lang)
        by_level = {lv: sum(1 for t in tasks if t['level'] == lv and t['id'] in solved) for lv in 'ABC'}
        level = 'A' if by_level['A'] < 5 else ('B' if by_level['B'] < 5 else 'C')      # растём постепенно: A → B → C
        last = (Submission.objects.filter(student=st, is_graded=True, score=10).order_by('-updated_at')
                .values_list('task_id', flat=True).first())
        last_tags = next((set(t['tags']) for t in tasks if t['id'] == last), set())
        free = [t for t in tasks if t['id'] not in solved]
        pick = (next((t for t in free if t['level'] == level and last_tags & set(t['tags'])), None)
                or next((t for t in free if t['level'] == level), None) or (free[0] if free else None))
        return JsonResponse({'task': pick['id'] if pick else None, 'level': level, 'solved': len(solved)})
    if q == 'topics':
        return JsonResponse({'topics': of.topics_for(st) if st else []})
    if q == 'theory':
        try:
            tid = int(request.GET.get('task'))
        except (TypeError, ValueError):
            return JsonResponse({'items': []})
        items = []
        for tp in Topic.objects.filter(tasks__id=tid).select_related('course'):
            if not _course_open_for(request.user, tp.course):
                continue
            url = reverse('topic_theory', args=[tp.id]) if tp.theory_html else reverse('course_detail', args=[tp.course_id])
            items.append({'title': f'{tp.course.name} — {tp.name}', 'url': url})
        return JsonResponse({'items': items[:3]})
    return JsonResponse({'error': 'bad'})
