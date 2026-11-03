# -*- coding: utf-8 -*-
"""Дисквалификация ученика из итогов четверти (например, использовал ИИ): убираем его из рейтинга и
Зала славы, возвращаем монеты-награду, снимаем баннер «Чемпион четверти» и оставляем уведомление."""
from django.db import transaction
from django.utils import timezone

from . import quarters


def disqualify(student, quarter_n, reason='', by=None):
    """Возвращает словарь с итогом: {'refunded': монеты, 'banner_removed': bool, 'was_place': место или None}."""
    from .models import CoinTransaction, HallEntry, QuarterExclusion, QuarterFreeze, StudentNotice
    q = quarters.BY_N[quarter_n]
    reason = (reason or '').strip()[:200] or 'использование ИИ при решении задач'
    frozen = QuarterFreeze.objects.filter(year=quarters.YEAR, quarter=quarter_n).exists()
    result = {'refunded': 0, 'banner_removed': False, 'was_place': None}
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
