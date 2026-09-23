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


def _limit_resources():
    """Выполняется в дочернем процессе (preexec_fn) перед запуском кода ученика."""
    if not HAS_RESOURCE:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (TIME_LIMIT_SECONDS, TIME_LIMIT_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        # Ограничение числа процессов — от "форк-бомб" в присланном коде.
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    except (ValueError, OSError):
        # Платформа/окружение не позволяет выставить лимит — продолжаем без
        # него: таймаут на уровне subprocess.run всё равно защищает от зависания.
        pass


def _run_one(code, stdin_text):
    """Запускает code одним процессом Python, скармливает stdin_text на stdin.
    Возвращает (stdout, error), где error — None, 'timeout', 'memory' или
    короткое сообщение об ошибке выполнения."""
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            input=stdin_text or '',
            capture_output=True,
            text=True,
            timeout=WALL_CLOCK_TIMEOUT,
            preexec_fn=_limit_resources if HAS_RESOURCE else None,
        )
    except subprocess.TimeoutExpired:
        return '', 'timeout'
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if proc.returncode != 0:
        stderr_text = proc.stderr or ''
        # SIGKILL/SIGXCPU — процесс убит ядром за превышение лимита CPU-времени
        # (RLIMIT_CPU c одинаковым soft/hard лимитом убивает почти сразу).
        if proc.returncode in (-9, -24):
            return '', 'timeout'
        # RLIMIT_AS обычно приводит к обычному MemoryError внутри интерпретатора
        # (malloc не смог выделить память), а не к убийству процесса ядром.
        if 'MemoryError' in stderr_text:
            return '', 'memory'
        stderr_tail = stderr_text.strip().splitlines()
        message = stderr_tail[-1] if stderr_tail else f'код завершения {proc.returncode}'
        return '', message[:300]

    return proc.stdout, None


def run_autotests(task, code):
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
    try:
        for case in test_cases:
            expected = str(case.get('output', ''))
            stdin_text = str(case.get('input', ''))
            actual, error = _run_one(code, stdin_text)
            ok = error is None and actual.strip() == expected.strip()
            if ok:
                passed += 1
            results.append({
                'ok': ok,
                'error': error,
                'expected': expected[:OUTPUT_PREVIEW_LIMIT],
                'actual': actual.strip()[:OUTPUT_PREVIEW_LIMIT],
            })
    except Exception:
        return {'available': False}

    return {'available': True, 'passed': passed, 'total': len(test_cases), 'results': results}
