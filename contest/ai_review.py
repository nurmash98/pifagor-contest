# -*- coding: utf-8 -*-
"""ИИ-проверка решения ученика при отправке: подсказка по баллу, обратная связь
и проверка, не списано ли решение с готового варианта на GitHub/в интернете.

ВАЖНО: это только ПОДСКАЗКА для ученика и преподавателя. Итоговый балл всё равно
ставит преподаватель вручную (Submission.score) — эта проверка не может и не должна
заменять его решение, ИИ может ошибаться (в том числе не находить или "находить"
несуществующее совпадение).

Требует переменные окружения ANTHROPIC_API_KEY и (опционально) ANTHROPIC_MODEL.
Если ключ не задан, is_configured() вернёт False и run_ai_review() ничего не
делает — сайт продолжает работать как обычно, просто без этой подсказки.

Стоимость: каждый вызов — это один запрос к Anthropic Messages API с включённым
веб-поиском (используется, чтобы искать готовые решения на GitHub/в интернете).
Веб-поиск оплачивается отдельно от обычных токенов (по состоянию на 2026 год —
порядка $10 за 1000 поисковых запросов), плюс обычная стоимость токенов запроса
и ответа. Проверка запускается один раз на каждую ОТПРАВКУ кода (не при каждом
открытии страницы) — см. ai_reviewed_at/last_submitted_at в contest/views.py.
"""
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def is_configured():
    """Настроен ли ИИ-ключ на этом сервере (в переменных окружения)."""
    return bool(getattr(settings, 'ANTHROPIC_API_KEY', '') and getattr(settings, 'ANTHROPIC_MODEL', ''))


def _build_prompt(task, code, lang='ru'):
    return (
        "Ты помогаешь проверять учебные решения школьников на Python на образовательной "
        "платформе. Вот условие задачи и код, который прислал ученик.\n\n"
        f"Название задачи: {task.get_display_title(lang)}\n\n"
        f"Условие:\n{task.get_display_description(lang)}\n\n"
        f"Пример входных данных:\n{task.get_display_input_example(lang)}\n\n"
        f"Пример выходных данных:\n{task.get_display_output_example(lang)}\n\n"
        f"Код ученика (Python):\n```python\n{code}\n```\n\n"
        "Сделай три вещи:\n"
        "1. Поищи в интернете (в первую очередь на GitHub) готовые публичные решения именно "
        "этой задачи, похожие на код ученика — это может означать, что решение списано, а не "
        "написано самостоятельно. Если находишь подходящий пример, обязательно укажи ссылку "
        "на него в поле plagiarism_note. Если ничего похожего не нашлось — оставь это поле "
        "пустой строкой. Не утверждай, что решение списано, если ты не нашёл конкретную ссылку.\n"
        "2. Оцени решение ученика по 10-балльной шкале (0 — совсем не работает или не по теме, "
        "10 — полностью верное и аккуратное решение).\n"
        "3. Дай короткую (2-4 предложения) обратную связь ученику на русском языке: что сделано "
        "верно, что можно улучшить. Обращайся напрямую к ученику, по-дружески.\n\n"
        "Ответь СТРОГО в виде одного JSON-объекта, без пояснений до или после него, в точности "
        "в таком формате:\n"
        '{"score": <целое число от 0 до 10>, "feedback": "<обратная связь для ученика>", '
        '"plagiarism_note": "<ссылка и краткое пояснение, или пустая строка>"}'
    )


def _extract_json(text):
    """Модель иногда оборачивает ответ в ```json ... ``` или добавляет текст вокруг —
    вырезаем сам JSON-объект между первой { и последней }."""
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


def run_ai_review(submission):
    """Запускает ИИ-проверку одной отправки решения. Ничего не бросает наружу —
    любая ошибка (нет ключа, сеть, лимиты, неразбираемый ответ) превращается в
    {'available': False, 'error': '...'}, чтобы это никогда не мешало ученику
    отправить решение или преподавателю открыть страницу проверки.

    Возвращает dict:
        {'available': True, 'score': int|None, 'feedback': str, 'plagiarism_note': str}
        или
        {'available': False, 'error': '<причина>'}
    """
    if not is_configured():
        return {'available': False, 'error': 'not_configured'}

    try:
        import anthropic
    except ImportError:
        logger.warning('Пакет anthropic не установлен — ИИ-проверка недоступна.')
        return {'available': False, 'error': 'package_missing'}

    task = submission.task
    code = submission.code or ''
    if not code.strip():
        return {'available': False, 'error': 'empty_code'}

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=[{"role": "user", "content": _build_prompt(task, code)}],
        )
    except Exception as exc:  # сеть, неверный ключ, лимиты и т.п. — не должно ронять отправку решения
        logger.exception('Ошибка при вызове ИИ-проверки решения #%s', submission.pk)
        return {'available': False, 'error': str(exc)}

    text_parts = [
        block.text for block in getattr(response, 'content', [])
        if getattr(block, 'type', None) == 'text'
    ]
    raw_text = ''.join(text_parts).strip()

    parsed = _extract_json(raw_text) if raw_text else None
    if parsed is None:
        logger.warning('Не удалось разобрать ответ ИИ-проверки решения #%s: %r', submission.pk, raw_text[:500])
        return {'available': False, 'error': 'unparseable_response'}

    score = parsed.get('score')
    try:
        score = max(0, min(10, int(score)))
    except (TypeError, ValueError):
        score = None

    return {
        'available': True,
        'score': score,
        'feedback': (parsed.get('feedback') or '').strip(),
        'plagiarism_note': (parsed.get('plagiarism_note') or '').strip(),
    }
