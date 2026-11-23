"""«Подготовка к районным олимпиадам»: 10 прошлых районных олимпиад (2-й этап РО по информатике, 2019–2026).
Условия и тесты — contest/data/district_olympiads.json.gz (тесты придуманы и проверены эталонными решениями).
Каждая олимпиада — Exam(kind='OLYM', practice=True): задачи скрыты из каталога, но открыты всем ученикам
в разделе «Олимпиада» для решения без ограничения времени."""
import gzip
import json
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data' / 'district_olympiads.json.gz'
TASK_FIELDS = ('level', 'description', 'input_example', 'output_example', 'examples', 'time_limit', 'test_cases')


def load(Exam, Task):
    """Создаёт/обновляет олимпиады и их задачи. Повторный запуск ничего не дублирует. Возвращает число олимпиад."""
    from django.utils import timezone
    contests = json.loads(gzip.decompress(DATA.read_bytes()).decode('utf-8'))
    for c in contests:
        date = c['date'] if ' ' in c['date'] else c['date'] + ' 10:00'
        start = timezone.make_aware(datetime.strptime(date, '%Y-%m-%d %H:%M'))
        exam = Exam.objects.filter(kind='OLYM', practice=True, title=c['title']).first()
        if exam is None:
            exam = Exam.objects.create(kind='OLYM', practice=True, upsolve=True, title=c['title'],
                                       school_class='', start_at=start, duration_minutes=180)
        tasks = []
        for t in c['tasks']:
            values = {f: t[f] for f in TASK_FIELDS}
            task = Task.objects.filter(title=t['title'], hidden=True).first()
            if task is None:
                task = Task.objects.create(title=t['title'], hidden=True, **values)
            else:
                for f, v in values.items():
                    setattr(task, f, v)
                task.save()
            tasks.append(task)
        exam.tasks.set(tasks)
    return len(contests)
