# -*- coding: utf-8 -*-
"""Автозапуск присланного кода ученика против тест-кейсов задачи (Task.test_cases).

Тест-кейсы задаёт админ в Django admin (JSON-поле test_cases у Task, формат
[{"input": "...", "output": "..."}, ...]) — учителя это поле не редактируют
(см. contest/admin.py: у них test_cases в readonly). Если у задачи нет
тест-кейсов, автотесты просто не запускаются — сайт работает как раньше.

Это ПОДСКАЗКА ученику и преподавателю, не финальная оценка — итоговый балл
всё равно ставит преподаватель вручную, как и раньше. Ученику показываем
только пройден/не пройден по каждому тесту (без ожидаемого вывода — иначе
можно было бы просто скопировать правильный ответ вместо решения задачи).
Преподавателю на странице проверки показываем детали (ожидалось/получено).

Безопасность и лимиты: каждый тест-кейс запускается ОТДЕЛЬНЫМ процессом
Python с ограничениями — не более TIME_LIMIT_SECONDS секунд процессорного
времени и не более MEMORY_LIMIT_BYTES памяti (виртуальный адрес процесса)
на процесс. Ограничения выставляются через resource.setrlimit в дочернем
процессе (POSIX/Linux — на сервере это так; если resource недоступен,
например при локальной разработке на другой ОС, лимит по памяти просто не
применяется, но лимит по времени всё равно есть — он обеспечивается
таймаутом самого subprocess.run, а не только RLIMIT_CPU).

ВАЖНО: это ограничивает время и память одного процесса, но НЕ является
полной изоляцией (нет отдельного контейнера, chroot или сетевой изоляции) —
защищает от случайных бесконечных циклов и утечек памяти в решении ученика,
но не рассчитано как защита от намеренно вредоносного кода.
"""
import os
import subprocess
import sys
import tempfile

try:
    import resource
    HAS_RESOURCE = True
except ImportError:  # не-POSIX платформа (например, Windows при локальной разработке)
    HAS_RESOURCE = False

TIME_LIMIT_SECONDS = 1
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024  # 256 МБ
# Небольшой запас поверх RLIMIT_CPU для таймаута subprocess.run — своего рода
# страховка на случай, если процесс не расходует CPU-время (например, спит),
# а не ограничение "вместо" RLIMIT_CPU.
WALL_CLOCK_TIMEOUT = TIME_LIMIT_SECONDS + 0.5
OUTPUT_PREVIEW_LIMIT = 2000  # сколько символов вывода сохраняем для показа преподавателю
MAX_TEST_CASES = 20  # разумный потолок, даже если в test_cases случайно окажется больше


def _tokens_match(a, b):
    if a.lower() == b.lower():
        return True
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    return abs(x - y) <= 1e-6 * max(1.0, abs(y))


def outputs_match(actual, expected):
    """Сравнение вывода ученика с ожидаемым — не придираемся к мелочам оформления:
    лишние пробелы/переносы строк не важны, регистр букв не важен, числа сравниваются
    как числа (7 == 7.0, 6.50 == 6.5). Слова, знаки препинания и сами числа — важны."""
    a, e = (actual or '').split(), (expected or '').split()
    return len(a) == len(e) and all(_tokens_match(x, y) for x, y in zip(a, e))


def _limit_resources(cpu=TIME_LIMIT_SECONDS):
    """Выполняется в дочернем процессе (preexec_fn) перед запуском кода ученика."""
    if not HAS_RESOURCE:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        # Ограничение числа процессов — от "форк-бомб" в присланном коде.
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    except (ValueError, OSError):
        # Платформа/окружение не позволяет выставить лимит — продолжаем без
        # него: таймаут на уровне subprocess.run всё равно защищает от зависания.
        pass


def _safe_env():
    """Окружение для кода ученика: ничего из окружения сервера (пароль базы, SECRET_KEY…)."""
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
    if os.name == 'nt':   # Windows (локальная разработка): без SYSTEMROOT Python не стартует
        env['SYSTEMROOT'] = os.environ.get('SYSTEMROOT', '')
    return env


def _sandbox_user():
    from django.conf import settings
    return getattr(settings, 'AUTOTEST_SANDBOX_USER', '') or ''


def build_command(tmp_path, sandbox_user=None, time_limit=None, target=None):
    """Команда запуска кода ученика. Возвращает (argv, wall_timeout, использовать_ли_preexec).
    Без песочницы — тот же Python, лимиты ставятся в дочернем процессе (preexec_fn).
    С песочницей (на сервере) — под отдельным пользователем без доступа к файлам приложения:
    sudo -> prlimit (лимиты CPU/память/процессы/файлы) -> timeout (убить по времени, даже если
    программа «спит») -> python -I."""
    cpu = int(time_limit or TIME_LIMIT_SECONDS)
    wall_clock = cpu + 0.5
    if not sandbox_user:
        return (target or [sys.executable, tmp_path]), wall_clock, True
    from django.conf import settings
    py = getattr(settings, 'AUTOTEST_SANDBOX_PYTHON', '/usr/bin/python3') or '/usr/bin/python3'
    argv = ['sudo', '-n', '-u', sandbox_user, '--', '/usr/bin/prlimit',
            f'--cpu={cpu}', f'--as={MEMORY_LIMIT_BYTES}', '--nproc=64', f'--fsize={10 * 1024 * 1024}', '--core=0',
            '/usr/bin/timeout', '-s', 'KILL', str(wall_clock)] + (target or [py, '-I', tmp_path])
    return argv, wall_clock + 2.5, False


def _run_one(code, stdin_text, time_limit=None, exe=None):
    """Запускает code одним процессом Python (или готовую программу exe — скомпилированный C++),
    скармливает stdin_text на stdin.
    Возвращает (stdout, error), где error — None, 'timeout', 'memory' или
    короткое сообщение об ошибке выполнения."""
    if exe:
        tmp_path = None
    else:
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
            f.write(code)
            tmp_path = f.name

    user = _sandbox_user()
    argv, wall, use_preexec = build_command(tmp_path, user, time_limit, target=[exe] if exe else None)
    cpu = int(time_limit or TIME_LIMIT_SECONDS)
    try:
        if user and tmp_path:
            os.chmod(tmp_path, 0o644)      # пользователь песочницы должен прочитать файл
        proc = subprocess.run(
            argv,
            input=stdin_text or '',
            capture_output=True,
            text=True,
            timeout=wall,
            env=_safe_env(),
            cwd=tempfile.gettempdir(),
            preexec_fn=(lambda: _limit_resources(cpu)) if (HAS_RESOURCE and use_preexec) else None,
        )
    except subprocess.TimeoutExpired:
        return '', 'timeout'
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if user and proc.returncode != 0 and (proc.stderr or '').lstrip().startswith(('sudo:', 'prlimit:')):
        return '', 'песочница недоступна (сообщите учителю)'
    if proc.returncode != 0:
        stderr_text = proc.stderr or ''
        # SIGKILL/SIGXCPU — процесс убит ядром за превышение лимита CPU-времени
        # (RLIMIT_CPU c одинаковым soft/hard лимитом убивает почти сразу).
        if proc.returncode in (-9, -24, 137, 152):   # 137/152 — то же через sudo/timeout (128 + сигнал)
            return '', 'timeout'
        # RLIMIT_AS обычно приводит к обычному MemoryError внутри интерпретатора
        # (malloc не смог выделить память), а не к убийству процесса ядром.
        if 'MemoryError' in stderr_text or 'bad_alloc' in stderr_text:
            return '', 'memory'
        if exe and proc.returncode in (-11, 139):
            return '', 'ошибка выполнения (выход за границы массива?)'
        stderr_tail = stderr_text.strip().splitlines()
        message = stderr_tail[-1] if stderr_tail else f'код завершения {proc.returncode}'
        return '', message[:300]

    return proc.stdout, None


LANGUAGES = {'python': 'Python 3', 'cpp': 'C++ 17'}
COMPILE_CPU_SECONDS = 10
COMPILE_MEMORY_BYTES = 1024 * 1024 * 1024
COMPILE_ERROR_LIMIT = 1500


def cpp_available():
    import shutil
    return shutil.which('g++') is not None


def compile_cpp(code):
    """Компилирует C++. Возвращает (путь к программе, папка, None) или (None, папка, текст ошибки).
    На сервере с песочницей компиляция идёт под её пользователем: #include "/путь/к/секрету"
    не покажет чужие файлы в сообщении об ошибке."""
    import shutil
    workdir = tempfile.mkdtemp(prefix='cpp-')
    os.chmod(workdir, 0o777)
    src, exe = os.path.join(workdir, 'main.cpp'), os.path.join(workdir, 'main')
    with open(src, 'w') as f:
        f.write(code)
    os.chmod(src, 0o644)
    gxx = shutil.which('g++') or '/usr/bin/g++'
    cmd = [gxx, '-O2', '-std=c++17', '-pipe', '-o', exe, src]
    user = _sandbox_user()
    if user:
        cmd = ['sudo', '-n', '-u', user, '--', '/usr/bin/prlimit', f'--cpu={COMPILE_CPU_SECONDS}',
               f'--as={COMPILE_MEMORY_BYTES}', '--nproc=64', f'--fsize={64 * 1024 * 1024}', '--core=0',
               '/usr/bin/timeout', '-s', 'KILL', str(COMPILE_CPU_SECONDS + 5)] + cmd

    def limits():
        if HAS_RESOURCE:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (COMPILE_CPU_SECONDS, COMPILE_CPU_SECONDS))
                resource.setrlimit(resource.RLIMIT_AS, (COMPILE_MEMORY_BYTES, COMPILE_MEMORY_BYTES))
            except (ValueError, OSError):
                pass
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_CPU_SECONDS + 10,
                              env=_safe_env(), cwd=workdir, preexec_fn=None if user else limits)
    except subprocess.TimeoutExpired:
        return None, workdir, 'компиляция заняла слишком много времени'
    if proc.returncode != 0 or not os.path.exists(exe):
        msg = (proc.stderr or proc.stdout or 'ошибка компиляции').replace(src, 'main.cpp').replace(workdir + '/', '')
        return None, workdir, msg[:COMPILE_ERROR_LIMIT]
    os.chmod(exe, 0o755)
    return exe, workdir, None


def run_autotests(task, code, language='python'):
    """Прогоняет code против всех test_cases задачи. Ничего не бросает наружу —
    любая непредвиденная ошибка превращается в {'available': False}.

    Возвращает dict:
        {'available': True, 'passed': int, 'total': int, 'results': [...]}
        или
        {'available': False}
    Каждый results[i] = {'ok': bool, 'error': None|'timeout'|'memory'|str,
                          'expected': str, 'actual': str} — expected/actual
    обрезаны до OUTPUT_PREVIEW_LIMIT символов (только для показа преподавателю,
    сравнение делается по полным строкам ДО обрезки).
    """
    test_cases = (task.test_cases or [])[:MAX_TEST_CASES]
    if not test_cases or not (code or '').strip():
        return {'available': False}

    results = []
    passed = 0
    limit = getattr(task, 'time_limit', None) or TIME_LIMIT_SECONDS
    exe = workdir = None
    if language == 'cpp':
        if not cpp_available():
            return {'available': False, 'compile_error': 'C++ на сервере недоступен (нет компилятора g++).'}
        exe, workdir, compile_error = compile_cpp(code)
        if compile_error:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
            results = [{'ok': False, 'error': 'compile', 'input': '', 'expected': str(c.get('output', ''))[:OUTPUT_PREVIEW_LIMIT],
                        'actual': '', 'group': c.get('group')} for c in test_cases]
            return {'available': True, 'passed': 0, 'total': len(test_cases), 'results': results,
                    'compile_error': compile_error}
    try:
        for case in test_cases:
            expected = str(case.get('output', ''))
            stdin_text = str(case.get('input', ''))
            actual, error = _run_one(code, stdin_text, limit, exe=exe)
            ok = error is None and outputs_match(actual, expected)
            if ok:
                passed += 1
            results.append({
                'ok': ok,
                'error': error,
                'input': stdin_text[:OUTPUT_PREVIEW_LIMIT],
                'expected': expected[:OUTPUT_PREVIEW_LIMIT],
                'actual': actual.strip()[:OUTPUT_PREVIEW_LIMIT],
                'group': case.get('group'),
            })
    except Exception:
        return {'available': False}
    finally:
        if workdir:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)

    return {'available': True, 'passed': passed, 'total': len(test_cases), 'results': results}


def group_summary(task, results):
    """Олимпиадная оценка по группам: [{'n', 'points', 'got', 'passed', 'total'}] и сумма баллов.
    Баллы группы засчитываются, только если пройдены все её тесты."""
    weights = list(getattr(task, 'test_groups', None) or [])
    groups = []
    for n, points in enumerate(weights, 1):
        mine = [r for r in results if r.get('group') == n]
        passed = sum(1 for r in mine if r.get('ok'))
        ok = bool(mine) and passed == len(mine)
        groups.append({'n': n, 'points': points, 'got': points if ok else 0, 'passed': passed, 'total': len(mine)})
    examples = [r for r in results if not r.get('group')]
    return groups, sum(g['got'] for g in groups), examples


def olympiad_points(task, result):
    """Баллы олимпиады за задачу (из 100): по группам тестов, а если групп нет — доля пройденных тестов."""
    if not result.get('available'):
        return 0
    if getattr(task, 'test_groups', None):
        return group_summary(task, result['results'])[1]
    total = result['total']
    return int(100 * result['passed'] / total + 0.5) if total else 0
