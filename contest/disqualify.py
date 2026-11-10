# -*- coding: utf-8 -*-
"""Дисквалификация ученика из итогов четверти (например, использовал ИИ): убираем его из рейтинга и
Зала славы, возвращаем монеты-награду, снимаем баннер «Чемпион четверти» и оставляем уведомление."""
from django.db import transaction
from django.utils import timezone

from . import quarters


def reconcile(quarter_n, notify=True):
    """Приводит награды зафиксированной четверти в соответствие с итоговой таблицей Зала славы (с учётом убранных):
    • монеты: у каждого ученика сумма наград четверти должна равняться награде за его место — лишнее
      (у убранных) возвращается, недостающее (у поднявшихся в топ или выше) доначисляется;
    • баннер «Чемпион четверти» получает ученик на 1 месте и снимается у убранных без других побед.
    Можно запускать сколько угодно раз: когда всё сходится, ничего не меняется.
    Возвращает {'refunded': [(имя, монеты)], 'awarded': [(имя, место, монеты)], 'champions': [имя], 'banners_removed': [имя]}."""
    from django.db.models import Q
    from . import banners as banner_module
    from .models import CoinTransaction, HallEntry, QuarterExclusion, QuarterFreeze, StudentNotice
    Y = quarters.YEAR
    out = {'refunded': [], 'awarded': [], 'champions': [], 'banners_removed': []}
    if not QuarterFreeze.objects.filter(year=Y, quarter=quarter_n).exists():
        return out
    q = quarters.BY_N[quarter_n]
    grades = {quarters.grade_of(ex.student.school_class)
              for ex in QuarterExclusion.objects.filter(year=Y, quarter=quarter_n).select_related('student')}
    for g in grades:                       # места после исключений могли не пересобраться — пересобираем
        quarters.rebuild_hall_students(q, g)
    keys = Q(key=f'q:{Y}:{quarter_n}') | Q(key=f'qrev:{Y}:{quarter_n}') | Q(key__startswith=f'qfix:{Y}:{quarter_n}:')
    entries = {e.student_id: e for e in HallEntry.objects.filter(year=Y, quarter=quarter_n, kind='student',
                                                                 student__isnull=False).select_related('student')}
    paid = {}
    for t in CoinTransaction.objects.filter(keys):
        paid[t.student_id] = paid.get(t.student_id, 0) + t.amount
    from .models import Student
    with transaction.atomic():
        excluded = set(QuarterExclusion.objects.filter(year=Y, quarter=quarter_n).values_list('student_id', flat=True))
        for sid in set(entries) | set(paid):
            e = entries.get(sid)
            have = paid.get(sid, 0)
            if e is not None:
                want = quarters.award_for(e.place, quarter_n)
                if want <= have:          # лишнее у не убранных не отбираем (ранг мог поменяться из-за поздних оценок)
                    continue
            else:
                if sid not in excluded or have <= 0:
                    continue              # вне топа, но не убран — награду не трогаем
                want = 0
            st = e.student if e else Student.objects.filter(pk=sid).first()
            if st is None:
                continue
            diff = want - have
            CoinTransaction.objects.get_or_create(
                student=st, key=f'qfix:{Y}:{quarter_n}:{have}>{want}',
                defaults=dict(kind='award', amount=diff, at=timezone.now(),
                              note=f'{quarter_n} четверть: награда пересчитана' + (f' ({e.place} место)' if e else '')))
            if diff < 0:
                out['refunded'].append((st.full_name, -diff))
            else:
                out['awarded'].append((st.full_name, e.place if e else None, diff))
                if notify:
                    StudentNotice.objects.create(
                        student=st,
                        text=f'Рейтинг {quarter_n} четверти пересчитан: ты на {e.place} месте. Тебе начислено {diff} монет.',
                        text_kk=f'{quarter_n} тоқсанның рейтингі қайта есептелді: сен {e.place}-орындасың. Саған {diff} тиын қосылды.')
        # баннер чемпиона
        for e in entries.values():
            if e.place == 1 and 'champion' not in (e.student.owned_banners or '').split(','):
                banner_module.give(e.student, 'champion')
                out['champions'].append(e.student.full_name)
                if notify:
                    StudentNotice.objects.create(
                        student=e.student, text=f'Ты чемпион {quarter_n} четверти среди своей параллели! Баннер «Чемпион четверти» теперь твой.',
                        text_kk=f'Сен {quarter_n} тоқсанның чемпионысың! «Тоқсан чемпионы» баннері енді сенікі.')
        for ex in QuarterExclusion.objects.filter(year=Y, quarter=quarter_n).select_related('student'):
            st = ex.student
            have = [k for k in (st.owned_banners or '').split(',') if k]
            if 'champion' in have and not HallEntry.objects.filter(year=Y, kind='student', student=st, place=1).exists():
                st.owned_banners = ','.join(k for k in have if k != 'champion')
                if st.name_banner == 'champion':
                    st.name_banner = ''
                st.save(update_fields=['owned_banners', 'name_banner'])
                out['banners_removed'].append(st.full_name)
    return out


def disqualify(student, quarter_n, reason='', by=None):
    """Возвращает словарь с итогом: {'refunded': монеты, 'banner_removed': bool, 'was_place': место или None}."""
    from .models import CoinTransaction, HallEntry, QuarterExclusion, QuarterFreeze, StudentNotice
    q = quarters.BY_N[quarter_n]
    reason = (reason or '').strip()[:200] or 'использование ИИ при решении задач'
    frozen = QuarterFreeze.objects.filter(year=quarters.YEAR, quarter=quarter_n).exists()
    result = {'refunded': 0, 'banner_removed': False, 'was_place': None, 'promoted': []}
    with transaction.atomic():
        QuarterExclusion.objects.get_or_create(
            student=student, year=quarters.YEAR, quarter=quarter_n, defaults={'reason': reason, 'created_by': by})
        entry = HallEntry.objects.filter(year=quarters.YEAR, quarter=quarter_n, kind='student', student=student).first()
        result['was_place'] = entry.place if entry else None
        grade = entry.grade if entry else quarters.grade_of(student.school_class)

        if frozen:
            # монеты: исходное начисление остаётся в истории, поверх него — отмена
            award = CoinTransaction.objects.filter(student=student, key=f'q:{quarters.YEAR}:{quarter_n}').first()
            if award and award.amount > 0:
                _, created = CoinTransaction.objects.get_or_create(
                    student=student, key=f'qrev:{quarters.YEAR}:{quarter_n}',
                    defaults=dict(kind='award', amount=-award.amount, at=timezone.now(),
                                  note=f'Награда {quarter_n} четверти отменена'))
                if created:
                    result['refunded'] = award.amount
            # баннер «Чемпион четверти» — если он был получен за это 1 место и других побед не осталось
            if entry and entry.place == 1:
                other_wins = (HallEntry.objects.filter(year=quarters.YEAR, kind='student', student=student, place=1)
                              .exclude(quarter=quarter_n).exists())
                have = [k for k in (student.owned_banners or '').split(',') if k]
                if not other_wins and 'champion' in have:
                    student.owned_banners = ','.join(k for k in have if k != 'champion')
                    if student.name_banner == 'champion':
                        student.name_banner = ''
                    student.save(update_fields=['owned_banners', 'name_banner'])
                    result['banner_removed'] = True
            quarters.rebuild_hall_students(q, grade)
            # те, кто поднялся в рейтинге: доначисляем награду за новое место, 1 место — баннер чемпиона
            fixed = reconcile(quarter_n)
            result['promoted'] = fixed['awarded']
            result['banner_removed'] = result['banner_removed'] or student.full_name in fixed['banners_removed']
            result['new_champions'] = fixed['champions']
        quarters.invalidate()

        parts_ru = [f'Тебя убрали из рейтинга {quarter_n} четверти. Причина: {reason}.']
        parts_kk = [f'Сені {quarter_n} тоқсанның рейтингінен алып тастады. Себебі: {reason}.']
        if result['refunded']:
            parts_ru.append(f'Монеты за награду четверти ({result["refunded"]}) возвращены.')
            parts_kk.append(f'Тоқсан марапаты үшін берілген тиындар ({result["refunded"]}) қайтарылды.')
        if result['banner_removed']:
            parts_ru.append('Баннер «Чемпион четверти» снят.')
            parts_kk.append('«Тоқсан чемпионы» баннері алынды.')
        StudentNotice.objects.create(student=student, text=' '.join(parts_ru), text_kk=' '.join(parts_kk))
    return result
