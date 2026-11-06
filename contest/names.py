# -*- coding: utf-8 -*-
"""Имена учеников: сортировка по фамилии (как в Edupage: «Фамилия Имя») и подбор имени из списка Edupage
по транслитерации — чтобы привести базу к тем же именам, не переписывая их вручную."""
import re
from difflib import SequenceMatcher

# Порядок букв как в Edupage (казахские буквы стоят рядом со своими русскими)
_ALPHABET = 'АӘБВГҒДЕЁЖЗИЙКҚЛМНҢОӨПРСТУҰҮФХҺЦЧШЩЪЫІЬЭЮЯ'
_ORDER = {ch: i for i, ch in enumerate(_ALPHABET)}


def name_key(name):
    """Ключ сортировки: по фамилии (первое слово), затем по имени, с казахским алфавитом; латиница — в конце."""
    words = re.sub(r'[,\s]+', ' ', (name or '').strip()).upper().split(' ')
    key = []
    for w in words[:2] + [''] * (2 - len(words[:2])):
        key.append(tuple(_ORDER.get(ch, 100 + ord(ch)) for ch in w))
    return tuple(key)


def sort_students(students, by_class=False):
    """Список учеников по фамилии (А→Я). by_class=True — сначала по классу."""
    if by_class:
        return sorted(students, key=lambda s: (s.school_class or '', name_key(s.full_name)))
    return sorted(students, key=lambda s: name_key(s.full_name))


_CYR = {'а': 'a', 'ә': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'ғ': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z',
        'и': 'i', 'й': 'i', 'і': 'i', 'к': 'k', 'қ': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'ң': 'n', 'о': 'o', 'ө': 'o',
        'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ұ': 'u', 'ү': 'u', 'ф': 'f', 'х': 'h', 'һ': 'h', 'ц': 'ts',
        'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ы': 'i', 'э': 'e', 'ю': 'iu', 'я': 'ia', 'ь': '', 'ъ': ''}


def _canon(word):
    """Слово → упрощённая латиница, одинаковая для кириллицы и английской записи (Aisultan ≈ Айсұлтан)."""
    w = word.lower()
    w = ''.join(_CYR.get(ch, ch) for ch in w)
    w = w.replace('x', 'ks').replace('kh', 'h').replace('j', 'zh').replace('y', 'i').replace('w', 'v').replace('q', 'k')
    w = w.replace('c', 'k').replace('ya', 'ia').replace('yu', 'iu')
    w = re.sub(r'[^a-z]', '', w)
    return re.sub(r'(.)\1+', r'\1', w)


def _tokens(name):
    return [t for t in (_canon(w) for w in re.split(r'[\s,._-]+', name or '')) if t]


def similarity(db_name, edu_name):
    """0..1: насколько имя в базе похоже на имя из Edupage (в любом порядке слов, с опечатками)."""
    a, b = _tokens(db_name), _tokens(edu_name)
    if not a or not b:
        return 0.0
    pairs = sorted(((SequenceMatcher(None, x, y).ratio(), i, j) for i, x in enumerate(a) for j, y in enumerate(b)), reverse=True)
    used_a, used_b, total = set(), set(), 0.0
    for r, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i); used_b.add(j); total += r
    return total / max(len(a), 1)


def best_matches(db_names, edu_names, threshold=0.72):
    """{индекс имени в базе: (индекс имени Edupage, оценка)} — каждому имени Edupage максимум один ученик."""
    scored = sorted(((similarity(d, e), i, j) for i, d in enumerate(db_names) for j, e in enumerate(edu_names)), reverse=True)
    res, used = {}, set()
    for s, i, j in scored:
        if s < threshold:
            break
        if i in res or j in used:
            continue
        res[i] = (j, s)
        used.add(j)
    return res
