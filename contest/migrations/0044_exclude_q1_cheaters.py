"""1 четверть 2026-2027: по решению учителя убрать из рейтинга трёх учеников (списывание).
Ищем по имени и фамилии в любом порядке, без учёта регистра, и по классу."""
from django.db import migrations

YEAR = '2026-2027'
PEOPLE = [
    ('Iskander Ryskulbekov', '7B'),
    ('Stepkin Saveliy', '7C'),
    ('Калманбет Абилмансур', '7K'),
]
LETTERS = {'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X',
           'Б': 'B', 'Д': 'D', 'Ф': 'F', 'Қ': 'K'}


def _cls(value):
    return ''.join(LETTERS.get(ch, ch) for ch in ''.join((value or '').split()).upper())


def _words(value):
    return sorted(w for w in (value or '').lower().replace('ё', 'е').split() if w)


def forward(apps, schema_editor):
    Student = apps.get_model('contest', 'Student')
    QuarterExclusion = apps.get_model('contest', 'QuarterExclusion')
    for name, cls in PEOPLE:
        want = _words(name)
        for st in Student.objects.all():
            if _words(st.full_name) == want and _cls(st.school_class) == _cls(cls):
                QuarterExclusion.objects.get_or_create(student=st, year=YEAR, quarter=1,
                                                       defaults={'reason': 'списывание'})


def backward(apps, schema_editor):
    QuarterExclusion = apps.get_model('contest', 'QuarterExclusion')
    QuarterExclusion.objects.filter(year=YEAR, quarter=1, reason='списывание').delete()


class Migration(migrations.Migration):

    dependencies = [('contest', '0043_quarter_exclusion')]

    operations = [migrations.RunPython(forward, backward)]
