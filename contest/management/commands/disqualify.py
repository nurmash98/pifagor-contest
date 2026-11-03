# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand, CommandError

from contest import quarters
from contest.disqualify import disqualify
from contest.models import HallEntry, QuarterFreeze


class Command(BaseCommand):
    help = ('Убрать учеников из итогов зафиксированной четверти (ИИ/списывание): вернуть монеты-награду, снять баннер '
            'чемпиона, показать уведомление. Пример: manage.py disqualify --quarter 1 --grade 7 --places 1,2 --reason "ИИ"')

    def add_arguments(self, parser):
        parser.add_argument('--quarter', type=int, required=True)
        parser.add_argument('--grade', required=True, help='Параллель, например 7')
        parser.add_argument('--places', required=True, help='Места через запятую, например 1,2')
        parser.add_argument('--reason', default='использование ИИ при решении задач')
        parser.add_argument('--dry-run', action='store_true', help='Только показать, кого уберём')

    def handle(self, *args, **o):
        if not QuarterFreeze.objects.filter(year=quarters.YEAR, quarter=o['quarter']).exists():
            raise CommandError('Эта четверть ещё не зафиксирована.')
        places = [int(p) for p in o['places'].split(',') if p.strip()]
        entries = list(HallEntry.objects.filter(year=quarters.YEAR, quarter=o['quarter'], kind='student',
                                                grade=o['grade'], place__in=places).select_related('student').order_by('place'))
        if not entries:
            raise CommandError('В Зале славы нет таких мест.')
        for e in entries:
            self.stdout.write(f'{e.place} место: {e.name} ({e.school_class}) — {e.score} баллов')
        if o['dry_run']:
            return
        # места сдвигаются после каждого убранного — поэтому берём учеников заранее
        for e in entries:
            if e.student is None:
                self.stdout.write(self.style.WARNING(f'{e.name}: аккаунт удалён, пропускаю'))
                continue
            res = disqualify(e.student, o['quarter'], o['reason'])
            self.stdout.write(self.style.SUCCESS(f'{e.name}: убран(а); монеты −{res["refunded"]}, баннер снят: {res["banner_removed"]}'))
