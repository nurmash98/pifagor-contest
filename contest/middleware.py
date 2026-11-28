# -*- coding: utf-8 -*-
import json
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

# Страницы, закрытые для ученика, пока он пишет СОР/СОЧ.
EXAM_LOCKED_VIEWS = {
    'all_tasks', 'task_detail',
    'courses', 'course_detail', 'topic_video', 'topic_theory',
    'olympiad_upsolve', 'olympiad_upsolve_task', 'room_enter', 'room_play', 'room_state', 'room_act', 'office_play', 'office_act',
}


# Страницы с задачами на Python — ученикам 1–4 классов вместо них Scratch-тренажёр.
JUNIOR_BLOCKED_VIEWS = {
    'all_tasks', 'task_detail', 'courses', 'course_detail', 'topic_video', 'topic_theory', 'subtopic_theory',
    'leaderboard', 'duels', 'olympiad_list',
}


class RealIPMiddleware:
    """За nginx на своём сервере адрес клиента приходит в заголовке X-Real-IP (nginx его перезаписывает,
    подделать нельзя). Берём его, только если запрос пришёл с самого сервера (127.0.0.1) и включено
    TRUST_X_REAL_IP — иначе ничего не меняем (Render, локальная разработка)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        if getattr(settings, 'TRUST_X_REAL_IP', False) and request.META.get('REMOTE_ADDR') in ('127.0.0.1', '::1'):
            real = (request.META.get('HTTP_X_REAL_IP') or '').strip()
            if real:
                request.META['REMOTE_ADDR'] = real
        return self.get_response(request)


class ExamLockMiddleware:
    """Пока у ученика идёт СОР/СОЧ (нажал «Начать», не завершил, время не вышло),
    Курсы и Все задачи недоступны — перекидываем обратно на экзамен.
    Идущая попытка кладётся в request.active_exam_attempt (для меню)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.active_exam_attempt = None
        user = getattr(request, 'user', None)
        polling = is_polling(request.path)            # частые опросы игр — без лишних запросов к базе
        if user is not None and not polling and user.is_authenticated and not user.is_staff:
            from .exam_views import active_attempt_for
            request.active_exam_attempt = active_attempt_for(user)
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, 'resolver_match', None)
        user = getattr(request, 'user', None)
        if match is not None and match.url_name in JUNIOR_BLOCKED_VIEWS and user is not None and user.is_authenticated and not user.is_staff:
            from .models import Student
            from .scratch_levels import is_junior
            if is_junior(Student.objects.filter(user=user).first()):
                return redirect('scratch_home')
        attempt = getattr(request, 'active_exam_attempt', None)
        match = getattr(request, 'resolver_match', None)
        if attempt is not None and match is not None and match.url_name in EXAM_LOCKED_VIEWS:
            messages.warning(request, 'Идёт СОР/СОЧ (или пересдача) — Курсы и Все задачи откроются после его окончания.')
            return redirect(attempt.get_absolute_url())
        return None


def is_polling(path):
    """Частые опросы игр (раз в 2–3 секунды): коворкинг и остров."""
    return path.endswith('/state/') and (path.startswith('/room/') or path.startswith('/office/') or path.startswith('/games/island/'))


from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402


class PollingSessionMiddleware(SessionMiddleware):
    """Как обычная сессия (SESSION_SAVE_EVERY_REQUEST продлевает её на каждом запросе), но частые опросы игр
    продлевают сессию не чаще раза в 5 минут — иначе каждый опрос писал бы в базу (Neon)."""

    def process_response(self, request, response):
        session = getattr(request, 'session', None)
        if session is not None and is_polling(request.path) and not session.modified and session.session_key:
            from django.core.cache import cache
            if not cache.add(f'sess:touch:{session.session_key}', 1, 300):
                return response
        return super().process_response(request, response)


class MovedSiteMiddleware:
    """Сайт переехал: если задан SITE_MOVED_TO (например, на старом Render —
    https://pifagor.infosabaq.kz), любой адрес отправляется туда же на ту же страницу (301)."""

    def __init__(self, get_response):
        from django.conf import settings
        self.get_response = get_response
        self.target = (getattr(settings, 'SITE_MOVED_TO', '') or '').rstrip('/')

    def __call__(self, request):
        if self.target:
            from django.http import HttpResponsePermanentRedirect
            return HttpResponsePermanentRedirect(self.target + request.get_full_path())
        return self.get_response(request)


class MaintenanceMiddleware:
    """«Вход воспрещён — технические неполадки»: до MAINTENANCE_UNTIL (UTC, ISO) на хостах MAINTENANCE_HOSTS
    все страницы, кроме входа, отдают 503 с сообщением. Учителя (staff) работают как обычно.
    После указанного времени сайт открывается сам — код можно не трогать."""

    def __init__(self, get_response):
        from datetime import datetime
        self.get_response = get_response
        until = (getattr(settings, 'MAINTENANCE_UNTIL', '') or '').strip()
        self.until = datetime.fromisoformat(until) if until else None
        self.hosts = {h.strip().lower() for h in getattr(settings, 'MAINTENANCE_HOSTS', ()) if h.strip()}

    def __call__(self, request):
        if self.until is not None and self._applies(request):
            from django.http import HttpResponse
            from django.utils import timezone
            now = timezone.now()
            if now < self.until:
                local = timezone.localtime(self.until).strftime('%H:%M')
                html = MAINTENANCE_HTML.replace('{until}', local)
                resp = HttpResponse(html, status=503)
                resp['Retry-After'] = str(int((self.until - now).total_seconds()) + 1)
                return resp
        return self.get_response(request)

    def _applies(self, request):
        host = request.get_host().split(':')[0].lower()
        if self.hosts and host not in self.hosts:
            return False
        path = request.path
        if path.startswith('/login') or path.startswith('/logout') or path.startswith('/static/') or path.startswith('/admin'):
            return False
        user = getattr(request, 'user', None)
        return not (user is not None and user.is_authenticated and user.is_staff)


MAINTENANCE_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Технические работы</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f3f5fb;color:#1a1a4b;padding:16px}
.box{max-width:460px;text-align:center;background:#fff;border-radius:20px;padding:36px 28px;box-shadow:0 8px 30px rgba(26,26,75,.12)}
.ic{font-size:56px}h1{font-size:26px;margin:12px 0 8px}p{color:#555;font-size:16px;line-height:1.5;margin:6px 0}</style></head>
<body><div class="box"><div class="ic">🚧</div><h1>Вход воспрещён</h1>
<p><b>Идут технические работы.</b> Сайт временно недоступен.</p>
<p>Попробуйте зайти после <b>{until}</b>.</p>
<p style="margin-top:14px">Кіру уақытша жабық — техникалық жұмыстар жүргізілуде.<br>{until} кейін кіріңіз.</p></div></body></html>"""


class MovedNoticeMiddleware:
    """Старый адрес (MOVED_NOTICE_HOSTS, например pifagor-contest.onrender.com): 3 секунды показываем
    «Сайт переехал на …» и переходим на ту же страницу нового сайта (MOVED_NOTICE_TO)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.target = (getattr(settings, 'MOVED_NOTICE_TO', '') or '').rstrip('/')
        self.hosts = {h.strip().lower() for h in getattr(settings, 'MOVED_NOTICE_HOSTS', ()) if h.strip()}

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()
        if self.target and host in self.hosts and not request.path.startswith('/static/'):
            import html
            from django.http import HttpResponse
            url = self.target + request.get_full_path()
            domain = self.target.split('://', 1)[-1]
            page = (MOVED_HTML.replace('{url_attr}', html.escape(url, quote=True))
                    .replace('{url_js}', json.dumps(url)).replace('{domain}', html.escape(domain)))
            resp = HttpResponse(page)
            resp['Cache-Control'] = 'no-store'
            return resp
        return self.get_response(request)


MOVED_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="3;url={url_attr}"><title>Сайт переехал</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f3f5fb;color:#1a1a4b;padding:16px}
.box{max-width:480px;text-align:center;background:#fff;border-radius:20px;padding:36px 28px;box-shadow:0 8px 30px rgba(26,26,75,.12)}
.ic{font-size:56px}h1{font-size:25px;margin:12px 0 8px}p{color:#555;font-size:16px;line-height:1.5;margin:6px 0}
.dom{display:inline-block;margin:10px 0 4px;font-size:22px;font-weight:700;color:#2e7d4f}
a.btn{display:inline-block;margin-top:14px;background:#1a1a4b;color:#fff;text-decoration:none;padding:11px 22px;border-radius:999px;font-weight:600}</style></head>
<body><div class="box"><div class="ic">🚚</div><h1>Сайт переехал</h1>
<p>Новый адрес — запомните его:</p><div class="dom">{domain}</div>
<p>Сайт көшті, жаңа мекенжай: <b>{domain}</b></p>
<p style="color:#888;font-size:14px">Переходим через <span id="s">3</span> с…</p>
<a class="btn" href="{url_attr}">Перейти сейчас →</a></div>
<script>var n=3,u={url_js};setInterval(function(){n--;if(n>=0)document.getElementById('s').textContent=n;if(n<=0)location.replace(u);},1000);</script>
</body></html>"""


class DailyVisitCoinMiddleware:
    """+1 монета ученику за вход на сайт — один раз в день (по времени Алматы).
    Считается любой открытый ученицей/учеником страница сайта (не опросы игр и не статика)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._award(request)
        except Exception:          # монета не должна ломать страницу
            pass
        return response

    def _award(self, request):
        from django.conf import settings
        if not getattr(settings, 'DAILY_VISIT_COIN', True):
            return
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated) or request.method != 'GET' or is_polling(request.path):
            return
        from django.core.cache import cache
        from django.utils import timezone
        day = timezone.localdate().isoformat()
        if not cache.add(f'visitcoin:{user.id}:{day}', 1, 26 * 3600):
            return
        from .models import CoinTransaction, Student
        st = Student.objects.filter(user=user).only('id').first()
        if st is None:
            return
        CoinTransaction.objects.get_or_create(student=st, key=f'visit:{day}',
                                              defaults=dict(kind='login', amount=1, note='Вход на сайт', at=timezone.now()))
        from .coins import award_streak
        award_streak(st.id)


class OnlineMiddleware:
    """Отмечает, когда ученик последний раз был на сайте (не чаще раза в минуту) — для счётчика
    «онлайн сейчас» у учителей. Опросы игр тоже считаются: ученик в игре — он онлайн."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and not user.is_staff:
            try:
                from django.core.cache import cache
                if cache.add(f'seen:{user.id}', 1, 60):
                    from django.utils import timezone
                    from .models import Student
                    Student.objects.filter(user_id=user.id).update(last_seen=timezone.now())
            except Exception:
                pass
        return response
