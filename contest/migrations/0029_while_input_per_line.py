"""Задачи на while: все входные значения — каждое на отдельной строке (без пробелов и запятых).
Меняем примеры ввода, тесты и фразы в условии, где говорилось «через пробел/запятую»."""
import re

from django.db import migrations

TITLES = [
    'Целочисленное деление вычитанием', 'Сколько дней копить', 'Сумма оценок до нуля',
    'НОД по алгоритму Евклида', 'Погашение кредита фиксированным платежом', 'Сокращение дроби',
    'Автомат с монетами', 'Быстрое возведение в степень', 'НОК двух чисел', 'НОД трёх чисел',
    'Подбор надёжного пароля', 'Угадай число за N попыток', 'Сумма до отрицательного числа',
    'Возведение в степень по модулю',
]
SUFFIX_RU = ' Каждое значение вводится на отдельной строке.'
SUFFIX_KK = ' Әр мән жеке жолда енгізіледі.'
# Задачи, где в условии уже есть своя формулировка про ввод — меняем её, суффикс не добавляем.
PHRASES = {
    'Автомат с монетами': [
        ('Вводится последовательность монет через пробел, их может быть больше, чем нужно.',
         'Монеты вводятся по одной на каждой строке, их может быть больше, чем нужно.'),
        ('Тиындар тізбегі бос орын арқылы енгізіледі', 'Тиындар әр жолға біреуден енгізіледі'),
    ],
    'Подбор надёжного пароля': [
        ('Дана последовательность попыток через запятую.', 'Попытки вводятся по одной на каждой строке.'),
        ('Әрекеттер тізбегі үтір арқылы берілген.', 'Әрекеттер әр жолға біреуден енгізіледі.'),
    ],
    'Угадай число за N попыток': [
        ('во второй — попытки через пробел', 'далее — попытки, каждая на отдельной строке'),
        ('екінші жолда әрекеттер бос орын арқылы енгізіледі',
         'одан кейін әрекеттер әр жолға біреуден енгізіледі'),
    ],
}


def per_line(text, keep_first_line=False):
    if not text:
        return text
    lines = text.split('\n')
    head = lines[:1] if keep_first_line else []
    rest = lines[1:] if keep_first_line else lines
    tokens = [t for t in re.split(r'[,\s]+', '\n'.join(rest)) if t]
    return '\n'.join(head + tokens)


def forward(apps, schema_editor):
    Task = apps.get_model('contest', 'Task')
    for task in Task.objects.filter(title__in=TITLES, tags__name='while').distinct():
        keep = task.title == 'Угадай число за N попыток'
        task.input_example = per_line(task.input_example, keep)
        task.input_example_kk = per_line(task.input_example_kk, keep) if task.input_example_kk else ''
        task.test_cases = [dict(c, input=per_line(c.get('input', ''), keep)) for c in (task.test_cases or [])]
        if task.title in PHRASES:
            for old, new in PHRASES[task.title]:
                task.description = task.description.replace(old, new)
                task.description_kk = task.description_kk.replace(old, new)
        else:
            if SUFFIX_RU.strip() not in task.description:
                task.description = task.description.rstrip() + SUFFIX_RU
            if task.description_kk and SUFFIX_KK.strip() not in task.description_kk:
                task.description_kk = task.description_kk.rstrip() + SUFFIX_KK
        task.save()


class Migration(migrations.Migration):

    dependencies = [("contest", "0028_easy_while_tasks")]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
