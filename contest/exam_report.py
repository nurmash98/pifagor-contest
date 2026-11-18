"""Анализ СОР/СОЧ в Word: по выбранным классам и четверти — отчёт в формате школы
(1. Жалпы ақпарат … 6. Ұсыныстар) с цифрами с платформы. Один класс — .docx, несколько — .zip."""
import io
import zipfile
from collections import defaultdict
from datetime import datetime

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from . import quarters
from .models import Exam, ExamAttempt, Student, Submission, Teacher

HIGH, MID = 8.5, 6.5                      # Жоғары 8,5–10 · Орташа 6,5–8,5 · Төмен < 6,5
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
YEAR_START = timezone.make_aware(datetime(2026, 9, 1))

T = {
    'kk': dict(
        title='ЕСЕП',
        sub_SOR='бөлім бойынша жиынтық бағалау (БЖБ) қорытындылары бойынша\n«Информатика» пәні',
        sub_SOCH='тоқсан бойынша жиынтық бағалау (ТЖБ) қорытындылары бойынша\n«Информатика» пәні',
        name_SOR='БЖБ', name_SOCH='ТЖБ',
        h1='1. Жалпы ақпарат', h2='{name} өткізу мақсаты', h3='3. Нәтижелерді талдау',
        h4='4. Оқушылардың типтік қиындықтары', h5='5. Қорытынды', h6='6. Ұсыныстар',
        cls='Сынып', q='Тоқсан', work='Жұмыс', date='Өткізілген күні', section='Бөлім',
        section_blank='________ (бөлім атауын көрсетіңіз)', teacher='Пән мұғалімі',
        goal_SOR='БЖБ оқушылардың тоқсан бөлімі бойынша оқу материалын меңгеру деңгейін анықтау мақсатында өткізілді.',
        goal_SOCH='ТЖБ оқушылардың тоқсан бойынша оқу материалын меңгеру деңгейін анықтау мақсатында өткізілді.',
        total='Оқушылардың жалпы саны', wrote='Жұмысты орындағандар', max='Ең жоғары балл', min='Ең төменгі балл',
        avg='{name} бойынша орташа балл', level='Деңгей', count='Оқушылар саны',
        lv_high='Жоғары (8,5-10)', lv_mid='Орташа (6,5-8,5)', lv_low='Төмен (6,5-ке дейін)',
        tasks_intro='Тапсырмалар бойынша нәтижелер:', task='Тапсырма', task_avg='Орташа балл (10-нан)',
        task_full='Толық шығарғандар', task_zero='0 балл алғандар',
        students_intro='Оқушылардың нәтижелері:', num='№', student='Оқушы', grade='Балл', lv='Деңгей',
        lvn_high='жоғары', lvn_mid='орташа', lvn_low='төмен', not_wrote='жазбады',
        gap='Формативті және жиынтық бағалау нәтижелері арасындағы алшақтық: формативті бағалау (платформадағы тапсырмалар) '
            'бойынша орташа балл — {f}, {name} бойынша — {s}',
        close='Формативті бағалау (платформадағы тапсырмалар) бойынша орташа балл — {f}, {name} бойынша — {s}: нәтижелер жақын',
        low_of='Төмен деңгейді (6,5 баллдан төмен) {n} оқушының ішінен {m} оқушы көрсетті',
        hardest='Ең көп қиындық тудырған тапсырма — {l} (орташа балл {a}, толық шығарғандар — {f} оқушы)',
        zeros='{n} оқушы кем дегенде бір тапсырманы мүлде шығармады (0 балл)',
        absent='Жұмысты жазбағандар: {names}',
        no_trouble='Елеулі қиындықтар анықталған жоқ',
        found='{name} нәтижелері бойынша анықталғаны:',
        levels_line='жоғары деңгей — {h} оқушы, орташа — {m}, төмен — {l}',
        lower='{name} нәтижесі формативті бағалау нәтижесінен төмен',
        higher='{name} нәтижесі формативті бағалау нәтижесінен жоғары',
        same='{name} нәтижесі формативті бағалау нәтижесімен шамалас',
        goals_full='оқу мақсаттары толық орындалды.', goals_most='оқу мақсаттары негізінен орындалды.',
        goals_part='оқу мақсаттары ішінара орындалды.',
        recs='Білім сапасын арттыру мақсатында мынадай ұсыныстар беріледі:',
        rec_repeat='бөлім материалын қайталау және {name} тапсырмаларын талдау',
        rec_practice='қосымша практикалық тапсырмалар өткізу',
        rec_hard='{l} тапсырмасына ұқсас есептерді сабақта қосымша талдау',
        rec_low='төмен нәтиже көрсеткен оқушылармен жеке жұмыс: {names}',
        rec_absent='жұмысты жазбаған оқушылармен қайта жазуды ұйымдастыру: {names}',
        rec_high='жоғары нәтиже көрсеткен оқушыларға күрделі (олимпиадалық) есептер ұсыну: {names}',
        file_one='{name} талдау {cls} {q} тоқсан', file_zip='БЖБ-ТЖБ талдау {q} тоқсан', none='Бұл тоқсанда {cls} сыныбында БЖБ/ТЖБ жоқ.',
    ),
    'ru': dict(
        title='ОТЧЁТ',
        sub_SOR='по итогам суммативного оценивания за раздел (СОР)\nпо предмету «Информатика»',
        sub_SOCH='по итогам суммативного оценивания за четверть (СОЧ)\nпо предмету «Информатика»',
        name_SOR='СОР', name_SOCH='СОЧ',
        h1='1. Общая информация', h2='Цель проведения {name}', h3='3. Анализ результатов',
        h4='4. Типичные затруднения учащихся', h5='5. Выводы', h6='6. Рекомендации',
        cls='Класс', q='Четверть', work='Работа', date='Дата проведения', section='Раздел',
        section_blank='________ (укажите название раздела)', teacher='Учитель',
        goal_SOR='СОР проведён с целью определения уровня усвоения учащимися учебного материала раздела четверти.',
        goal_SOCH='СОЧ проведён с целью определения уровня усвоения учащимися учебного материала за четверть.',
        total='Всего учащихся', wrote='Выполнили работу', max='Наивысший балл', min='Наименьший балл',
        avg='Средний балл за {name}', level='Уровень', count='Количество учащихся',
        lv_high='Высокий (8,5-10)', lv_mid='Средний (6,5-8,5)', lv_low='Низкий (до 6,5)',
        tasks_intro='Результаты по заданиям:', task='Задание', task_avg='Средний балл (из 10)',
        task_full='Решили полностью', task_zero='Получили 0 баллов',
        students_intro='Результаты учащихся:', num='№', student='Учащийся', grade='Балл', lv='Уровень',
        lvn_high='высокий', lvn_mid='средний', lvn_low='низкий', not_wrote='не писал(а)',
        gap='Расхождение между результатами формативного и суммативного оценивания: средний балл по формативному '
            'оцениванию (задачи на платформе) — {f}, по {name} — {s}',
        close='Средний балл по формативному оцениванию (задачи на платформе) — {f}, по {name} — {s}: результаты близки',
        low_of='Низкий уровень (ниже 6,5 балла) показали {m} из {n} учащихся',
        hardest='Наибольшее затруднение вызвало задание {l} (средний балл {a}, полностью решили — {f} уч.)',
        zeros='{n} уч. не решили хотя бы одно задание совсем (0 баллов)',
        absent='Не писали работу: {names}',
        no_trouble='Существенных затруднений не выявлено',
        found='По результатам {name} выявлено:',
        levels_line='высокий уровень — {h} уч., средний — {m}, низкий — {l}',
        lower='результат {name} ниже результатов формативного оценивания',
        higher='результат {name} выше результатов формативного оценивания',
        same='результат {name} сопоставим с результатами формативного оценивания',
        goals_full='цели обучения достигнуты полностью.', goals_most='цели обучения в основном достигнуты.',
        goals_part='цели обучения достигнуты частично.',
        recs='С целью повышения качества знаний рекомендуется:',
        rec_repeat='повторить материал раздела и разобрать задания {name}',
        rec_practice='провести дополнительные практические задания',
        rec_hard='дополнительно разобрать на уроке задачи, похожие на задание {l}',
        rec_low='индивидуальная работа с учащимися, показавшими низкий результат: {names}',
        rec_absent='организовать написание работы для не писавших: {names}',
        rec_high='предложить учащимся с высоким результатом задачи повышенной сложности (олимпиадные): {names}',
        file_one='Анализ {name} {cls} {q} четверть', file_zip='Анализ СОР-СОЧ {q} четверть', none='В этой четверти у {cls} нет СОР/СОЧ.',
    ),
}


def num(x):
    """5.36 → «5,36», 10.0 → «10»."""
    if x is None:
        return '—'
    s = f'{x:.2f}'.rstrip('0').rstrip('.')
    return s.replace('.', ',')


def level(g):
    return 'high' if g >= HIGH else ('mid' if g >= MID else 'low')


def quarter_exams(classes, q, kinds):
    """СОР/СОЧ классов в четверти (уже начавшиеся), по дате."""
    from .exam_views import exam_quarter
    now = timezone.now()
    out = defaultdict(list)
    for e in Exam.objects.filter(school_class__in=classes, kind__in=kinds, start_at__lte=now).order_by('start_at'):
        if exam_quarter(e) == q:
            out[e.school_class].append(e)
    return out


def formative_avg(students, q):
    """Средний балл задач на платформе (0–10) у этих учеников за четверть — «формативное оценивание»."""
    win = quarters.BY_N.get(q)
    start = (win and win['start']) or YEAR_START
    end = win['end'] if win else timezone.now()
    scores = list(Submission.objects.filter(student__in=students, is_graded=True, updated_at__gte=start,
                                            updated_at__lte=end).values_list('score', flat=True))
    return round(sum(s or 0 for s in scores) / len(scores), 2) if scores else None


def analyse(exam):
    """Все цифры для отчёта по одному СОР/СОЧ."""
    students = list(Student.objects.filter(school_class=exam.school_class).order_by('full_name'))
    attempts = {a.student_id: a for a in ExamAttempt.objects.filter(exam=exam, retake__isnull=True)
                .select_related('exam').prefetch_related('answers')}
    rows, wrote = [], []
    per_task = defaultdict(lambda: dict(scores=[], titles=set()))
    zero_students = 0
    for s in students:
        a = attempts.get(s.id)
        if a is None or a.started_at is None:
            rows.append(dict(name=s.full_name, grade=None))
            continue
        tasks = a.task_list()
        ans = {x.task_id: x for x in a.answers.all()}
        got = []
        for i, t in enumerate(tasks):
            sc = ans[t.id].best_score if t.id in ans else 0
            got.append(sc)
            slot = per_task[LETTERS[i] if i < len(LETTERS) else str(i + 1)]
            slot['scores'].append(sc)
            slot['titles'].add(t.title)
        g = round(sum(got) / len(got), 1) if got else 0.0
        if got and min(got) == 0:
            zero_students += 1
        rows.append(dict(name=s.full_name, grade=g))
        wrote.append((s.full_name, g))
    grades = [g for _, g in wrote]
    task_rows = [dict(letter=k, avg=round(sum(v['scores']) / len(v['scores']), 2) if v['scores'] else 0,
                      full=sum(1 for x in v['scores'] if x >= 10), zero=sum(1 for x in v['scores'] if x == 0),
                      n=len(v['scores']), titles=sorted(v['titles'])) for k, v in per_task.items()]
    return dict(
        exam=exam, students=students, rows=rows, total=len(students), wrote=len(wrote),
        max=max(grades) if grades else None, min=min(grades) if grades else None,
        avg=round(sum(grades) / len(grades), 2) if grades else None,
        high=[n for n, g in wrote if level(g) == 'high'], mid=[n for n, g in wrote if level(g) == 'mid'],
        low=[n for n, g in wrote if level(g) == 'low'], absent=[r['name'] for r in rows if r['grade'] is None],
        tasks=task_rows, zero_students=zero_students,
    )


# ---------------------------------------------------------------- Word
def _lines(doc, lines, style=None):
    """Абзац со строками через перенос (как в образце школы)."""
    p = doc.add_paragraph(style=style)
    for i, line in enumerate(lines):
        r = p.add_run(line)
        if i < len(lines) - 1:
            r.add_break()
    return p


def _table(doc, head, body, widths=None):
    from docx.shared import Cm
    t = doc.add_table(rows=1, cols=len(head))
    t.style = 'Table Grid'
    for i, h in enumerate(head):
        c = t.rows[0].cells[i]
        c.text = ''
        c.paragraphs[0].add_run(str(h)).bold = True
    for row in body:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    if widths:
        t.autofit = False
        for i, w in enumerate(widths):
            t.columns[i].width = Cm(w)
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    doc.add_paragraph()
    return t


def write_report(doc, info, q, lang, teacher_name, page_break=False):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.text import WD_BREAK
    L = T[lang]
    e = info['exam']
    kind = 'SOR' if e.kind == 'SOR' else 'SOCH'
    name = L['name_' + kind]
    if page_break:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph(L['title'], style='Title').alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = _lines(doc, L['sub_' + kind].split('\n'))
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_heading(L['h1'], level=1)
    info_lines = [f"{L['cls']}: {e.school_class}", f"{L['q']}: {q}", f"{L['work']}: {e.title}",
                  f"{L['date']}: {timezone.localtime(e.start_at).strftime('%d.%m.%Y')}"]
    if kind == 'SOR':
        info_lines.append(f"{L['section']}: {L['section_blank']}")
    info_lines.append(f"{L['teacher']}: {teacher_name or '________'}")
    _lines(doc, info_lines)

    doc.add_heading('2. ' + L['h2'].format(name=name), level=1)
    doc.add_paragraph(L['goal_' + kind])

    doc.add_heading(L['h3'], level=1)
    _lines(doc, [f"{L['total']}: {info['total']}", f"{L['wrote']}: {info['wrote']}",
                 f"{L['max']}: {num(info['max'])}", f"{L['min']}: {num(info['min'])}",
                 f"{L['avg'].format(name=name)}: {num(info['avg'])}"])
    _table(doc, [L['level'], L['count']], [[L['lv_high'], len(info['high'])], [L['lv_mid'], len(info['mid'])],
                                          [L['lv_low'], len(info['low'])]], widths=[9, 6])
    if info['tasks']:
        doc.add_paragraph(L['tasks_intro'])
        _table(doc, [L['task'], L['task_avg'], L['task_full'], L['task_zero']],
               [[t['letter'], num(t['avg']), f"{t['full']} / {t['n']}", f"{t['zero']} / {t['n']}"] for t in info['tasks']],
               widths=[3, 4.5, 4, 4])
    doc.add_paragraph(L['students_intro'])
    body = []
    for i, r in enumerate(info['rows'], 1):
        g = r['grade']
        body.append([i, r['name'], num(g) if g is not None else '—',
                     L['lvn_' + level(g)] if g is not None else L['not_wrote']])
    _table(doc, [L['num'], L['student'], L['grade'], L['lv']], body, widths=[1.2, 8.5, 2.5, 3.5])

    # 4. Қиындықтар
    doc.add_heading(L['h4'], level=1)
    fa = info['formative']
    trouble = []
    if fa is not None and info['avg'] is not None and fa - info['avg'] >= 0.5:     # СОР заметно ниже, чем задачи на платформе
        trouble.append(L['gap'].format(f=num(fa), s=num(info['avg']), name=name))
    if info['low']:
        trouble.append(L['low_of'].format(n=info['wrote'], m=len(info['low'])))
    hard = min(info['tasks'], key=lambda t: t['avg']) if len(info['tasks']) > 1 else None
    if hard and hard['avg'] < 8.5:
        trouble.append(L['hardest'].format(l=hard['letter'], a=num(hard['avg']), f=hard['full']))
    if info['zero_students']:
        trouble.append(L['zeros'].format(n=info['zero_students']))
    if info['absent']:
        trouble.append(L['absent'].format(names=', '.join(info['absent'])))
    _lines(doc, ['- ' + x for x in (trouble or [L['no_trouble']])])

    # 5. Қорытынды
    doc.add_heading(L['h5'], level=1)
    concl = [L['levels_line'].format(h=len(info['high']), m=len(info['mid']), l=len(info['low']))]
    if fa is not None and info['avg'] is not None:
        diff = info['avg'] - fa
        concl.append(L['lower' if diff <= -0.5 else ('higher' if diff >= 0.5 else 'same')].format(name=name))
    avg = info['avg'] or 0
    concl.append(L['goals_full'] if avg >= HIGH else (L['goals_most'] if avg >= MID else L['goals_part']))
    _lines(doc, [L['found'].format(name=name)] + ['- ' + x for x in concl])

    # 6. Ұсыныстар
    doc.add_heading(L['h6'], level=1)
    recs = [L['rec_repeat'].format(name=name), L['rec_practice']]
    if hard and hard['avg'] < 8.5:
        recs.append(L['rec_hard'].format(l=hard['letter']))
    if info['low']:
        recs.append(L['rec_low'].format(names=', '.join(info['low'])))
    if info['absent']:
        recs.append(L['rec_absent'].format(names=', '.join(info['absent'])))
    if info['high']:
        recs.append(L['rec_high'].format(names=', '.join(info['high'])))
    _lines(doc, [L['recs']] + ['- ' + x for x in recs])


def class_docx(exams, q, lang, teacher_name):
    from docx import Document
    doc = Document()
    for i, e in enumerate(exams):
        info = analyse(e)
        info['formative'] = formative_avg(info['students'], q)
        write_report(doc, info, q, lang, teacher_name or (e.created_by.full_name if e.created_by else ''), page_break=i > 0)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _safe(name):
    return ''.join(ch for ch in name if ch not in '\\/:*?"<>|').strip()


# ---------------------------------------------------------------- страница
def _can(user):
    return user.is_active and (user.is_superuser or user.is_staff)


@login_required
@user_passes_test(_can, login_url='/')
def exam_report(request):
    """Анализ СОР/СОЧ: выбираешь классы, четверть, СОР/СОЧ и язык — скачиваешь Word (несколько классов — zip)."""
    from .views import _get_content_lang, _managed_classes
    scope = _managed_classes(request.user)
    all_classes = sorted({c for c in Student.objects.values_list('school_class', flat=True) if c}
                         if scope is None else set(scope))
    all_classes.sort(key=lambda c: (int(quarters.grade_of(c)) if str(quarters.grade_of(c)).isdigit() else 99, c))
    teacher = Teacher.objects.filter(user=request.user).first()
    cur = quarters.current()
    if request.method == 'POST':
        classes = [c for c in request.POST.getlist('classes') if c in all_classes]
        kinds = [k for k in request.POST.getlist('kinds') if k in ('SOR', 'SOCH')]
        lang = request.POST.get('lang') if request.POST.get('lang') in ('ru', 'kk') else 'kk'
        try:
            q = int(request.POST.get('quarter'))
        except (TypeError, ValueError):
            q = 0
        if not classes or not kinds or q not in (1, 2, 3, 4):
            messages.error(request, 'Выберите хотя бы один класс, четверть и СОР или СОЧ.')
            return redirect('exam_report')
        found = quarter_exams(classes, q, kinds)
        if not any(found.values()):
            messages.warning(request, 'В выбранной четверти у этих классов нет проведённых СОР/СОЧ.')
            return redirect('exam_report')
        L = T[lang]
        name = '-'.join(L['name_' + k] for k in ('SOR', 'SOCH') if k in kinds)
        tname = teacher.full_name if teacher else ''
        files = [(c, class_docx(found[c], q, lang, tname)) for c in classes if found.get(c)]
        if len(files) == 1:
            c, data = files[0]
            resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            fname = _safe(L['file_one'].format(name=name, cls=c, q=q)) + '.docx'
        else:
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as z:
                for c, data in files:
                    z.writestr(_safe(L['file_one'].format(name=name, cls=c, q=q)) + '.docx', data)
            resp = HttpResponse(zbuf.getvalue(), content_type='application/zip')
            fname = _safe(L['file_zip'].format(q=q)) + '.zip'
        from urllib.parse import quote
        resp['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(fname)}"
        return resp

    # что есть по каждому классу и четверти — подсказки на плитках
    from .exam_views import exam_quarter
    avail = defaultdict(lambda: defaultdict(list))
    for e in Exam.objects.filter(school_class__in=all_classes, kind__in=['SOR', 'SOCH'], start_at__lte=timezone.now()):
        avail[e.school_class][exam_quarter(e)].append(e.kind)
    hints = {c: {q: {'SOR': v.count('SOR'), 'SOCH': v.count('SOCH')} for q, v in qs.items()} for c, qs in avail.items()}
    return render(request, 'exam_report.html', {
        'classes': all_classes, 'quarter': cur['n'] if cur else 1, 'lang': _get_content_lang(request),
        'hints': hints,
    })
