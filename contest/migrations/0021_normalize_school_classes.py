from django.db import migrations

# Копия правила из contest/models.py (normalize_school_class) — в миграции своя копия,
# чтобы будущие изменения кода не меняли то, что делает эта миграция.
LETTERS = {
    'А': 'A', 'В': 'B', 'Е': 'E', 'Ё': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O',
    'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X', 'У': 'U',
    'Б': 'B', 'Г': 'G', 'Д': 'D', 'З': 'Z', 'И': 'I', 'Й': 'I', 'Л': 'L', 'П': 'P',
    'Ф': 'F', 'Ц': 'C', 'Э': 'E', 'Ы': 'Y',
    'Ә': 'A', 'Ғ': 'G', 'Қ': 'K', 'Ң': 'N', 'Ө': 'O', 'Ұ': 'U', 'Ү': 'U', 'Һ': 'H', 'І': 'I',
}


def norm(value):
    text = ''.join((value or '').split()).upper()
    return ''.join(LETTERS.get(ch, ch) for ch in text)


def normalize(apps, schema_editor):
    """Классы в единый вид: '7ф', '7 f', '7 F' → '7F' — у учеников, у учителей (список
    классов) и у баллов классам, чтобы все записи одного класса совпадали."""
    Student = apps.get_model('contest', 'Student')
    Teacher = apps.get_model('contest', 'Teacher')
    ClassBonus = apps.get_model('contest', 'ClassBonus')
    for model in (Student, ClassBonus):
        for obj in model.objects.all():
            new = norm(obj.school_class)
            if new != obj.school_class:
                obj.school_class = new
                obj.save(update_fields=['school_class'])
    for teacher in Teacher.objects.all():
        classes = ', '.join(dict.fromkeys(norm(c) for c in teacher.classes.split(',') if c.strip()))
        if classes != teacher.classes:
            teacher.classes = classes
            teacher.save(update_fields=['classes'])


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0020_class_labels'),
    ]

    operations = [
        migrations.RunPython(normalize, migrations.RunPython.noop),
    ]
