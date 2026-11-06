# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand

from contest import quarters
from contest.disqualify import reconcile
from contest.models import QuarterFreeze


class Command(BaseCommand):
    help = ('Привести награды зафиксированных четвертей в порядок: вернуть монеты убранным, доначислить поднявшимся, '
            'выдать баннер чемпиона новому 1 месту. Безопасно запускать повторно.')

    def handle(self, *args, **o):
        for n in sorted(QuarterFreeze.objects.filter(year=quarters.YEAR).values_list('quarter', flat=True)):
            r = reconcile(n)
            self.stdout.write(f'{n} четверть: возвращено {r["refunded"]}; начислено {r["awarded"]}; '
                              f'новые чемпионы {r["champions"]}; баннер снят {r["banners_removed"]}')
