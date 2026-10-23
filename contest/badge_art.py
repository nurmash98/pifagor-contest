"""Рисунки значков за задачи по материалам (дерево → бронза → серебро → золото → алмаз → легенда)
и данные для рамки аватара. SVG собирается здесь, чтобы не держать сложную графику в шаблонах."""
import math

from django.utils.html import escape
from django.utils.safestring import mark_safe

TIERS = {
    't10': dict(tier='wood', c=('#d9a066', '#a86b35', '#6e4220'), rib=('#b47a44', '#7a4b23'),
                icon='sprout', rivets=True, grain=True),
    't50': dict(tier='bronze', c=('#f3b880', '#c47b3c', '#7a461b'), rib=('#c98444', '#874e1f'),
                icon='bolt', rivets=True),
    't100': dict(tier='silver', c=('#ffffff', '#c3cad6', '#7d8797'), rib=('#d5dbe4', '#8a93a3'),
                 icon='code', shine=True),
    't200': dict(tier='gold', c=('#fff1b8', '#f2c230', '#a8740a'), rib=('#f5cf4a', '#b8860b'),
                 icon='star', shine=True, wreath='#e8b923'),
    't500': dict(tier='diamond', c=('#f0fdff', '#8fe3f5', '#2d8fb3'), rib=('#9be7f7', '#2f8db0'),
                 icon='trophy', shine=True, facets=True, wreath='#7fd8ee'),
    't1000': dict(tier='legend', c=('#d8b4fe', '#7c3aed', '#2e1065'), rib=('#8b5cf6', '#3b0f7a'),
                  icon='flame', shine=True, wreath='#fbbf24', fire=True),
}

HEX = '70,10 118,37 118,93 70,120 22,93 22,37'
INNER = '70,20 109,42 109,88 70,110 31,88 31,42'

ICONS = {
    'sprout': '<path d="M70 74 V56" stroke="#3f7d2a" stroke-width="4" stroke-linecap="round"/>'
              '<ellipse cx="60" cy="54" rx="11" ry="6" transform="rotate(-25 60 54)" fill="#6fcf4a" stroke="#3f7d2a" stroke-width="1.5"/>'
              '<ellipse cx="80" cy="50" rx="12" ry="6.5" transform="rotate(25 80 50)" fill="#7fdc58" stroke="#3f7d2a" stroke-width="1.5"/>',
    'bolt': '<polygon points="76,28 52,62 67,62 60,88 90,50 74,50 84,28" fill="#ffe08a" stroke="#5a3a1a" stroke-width="2" stroke-linejoin="round"/>',
    'code': '<rect x="47" y="33" width="46" height="34" rx="5" fill="#2b3a55" stroke="#eef2f7" stroke-width="2.5"/>'
            '<text x="70" y="56" text-anchor="middle" font-family="monospace" font-size="15" font-weight="700" fill="#7fffd4">&lt;/&gt;</text>'
            '<rect x="41" y="69" width="58" height="6" rx="2.5" fill="#eef2f7"/>',
    'star': '<polygon points="70,30 77,46 94,47 81,58 85,75 70,66 55,75 59,58 46,47 63,46" fill="#fff6cc" stroke="#9a6a05" stroke-width="2" stroke-linejoin="round"/>',
    'trophy': '<path d="M56 34 H84 V48 Q84 64 70 66 Q56 64 56 48Z" fill="#fff" stroke="#1f6f8f" stroke-width="2"/>'
              '<path d="M56 38 H48 Q46 52 58 54 M84 38 H92 Q94 52 82 54" stroke="#1f6f8f" stroke-width="2.6" fill="none"/>'
              '<rect x="66" y="66" width="8" height="7" fill="#1f6f8f"/><rect x="58" y="73" width="24" height="6" rx="2" fill="#1f6f8f"/>',
    'flame': '<path d="M70 28 Q84 44 80 56 Q88 52 86 44 Q98 58 90 72 Q84 82 70 82 Q54 82 50 70 Q46 58 56 48 Q56 58 62 60 Q58 44 70 28Z" fill="#fb923c" stroke="#fde68a" stroke-width="2"/>'
             '<path d="M70 50 Q78 60 76 68 Q74 76 70 76 Q64 76 63 70 Q62 62 70 50Z" fill="#fde047"/>',
    'lock': '<rect x="56" y="48" width="28" height="22" rx="4" fill="#fff" fill-opacity=".85"/>'
            '<path d="M62 48 V41 Q62 33 70 33 Q78 33 78 41 V48" stroke="#fff" stroke-opacity=".85" stroke-width="4" fill="none"/>',
}


def _wreath(color):
    out = []
    for side in (-1, 1):
        for i in range(7):
            ang = math.radians(i * 15 + 10)
            x = 70 + side * (54 * math.cos(ang - 0.2) + 2)
            y = 70 + 54 * math.sin(ang + 0.1)
            out.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="7" ry="3.2" transform="rotate({side * (i * 15 + 70)} {x:.1f} {y:.1f})" '
                       f'fill="{color}" stroke="rgba(0,0,0,.25)" stroke-width=".8"/>')
    return ''.join(out)


def tier_svg(badge, uid='x', earned=True, width=None, height=None):
    """SVG значка за задачи. badge — запись каталога с полями TIERS, need и title/ru."""
    t = TIERS[badge['key']]
    g = f"bt-{badge['key']}-{uid}"
    c0, c1, c2 = t['c']
    size = f' width="{width}" height="{height}"' if width else ''
    title = escape(badge.get('title') or badge.get('ru') or '')
    s = [f'<svg viewBox="0 0 140 156"{size} aria-hidden="true" class="tier-badge{(" tier-" + t["tier"]) if earned else ""}">'
         f'<defs><linearGradient id="{g}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c0}"/>'
         f'<stop offset=".5" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>'
         f'<linearGradient id="r{g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t["rib"][0]}"/>'
         f'<stop offset="1" stop-color="{t["rib"][1]}"/></linearGradient>'
         f'<clipPath id="c{g}"><polygon points="{HEX}"/></clipPath></defs>']
    if t.get('fire') and earned:
        for x, y, r in ((40, 30, -20), (100, 30, 20), (26, 70, -40), (114, 70, 40), (70, 4, 0)):
            s.append(f'<path d="M{x} {y + 14} Q{x - 8} {y} {x} {y - 12} Q{x + 8} {y} {x} {y + 14}Z" '
                     f'transform="rotate({r} {x} {y})" fill="#f97316" opacity=".85"/>')
    if t.get('wreath'):
        s.append(_wreath(t['wreath']))
    s.append(f'<polygon points="{HEX}" fill="url(#{g})" stroke="{c2}" stroke-width="3"/><g clip-path="url(#c{g})">')
    if t.get('grain'):
        for i, y in enumerate((30, 44, 58, 72, 86, 100)):
            s.append(f'<path d="M20 {y} Q45 {y + (5 if i % 2 else -5)} 70 {y} T120 {y}" stroke="#5a3415" stroke-opacity=".35" stroke-width="2" fill="none"/>')
        s.append('<ellipse cx="94" cy="66" rx="6" ry="3.5" fill="none" stroke="#5a3415" stroke-opacity=".4" stroke-width="1.6"/>')
    if t.get('facets'):
        s.append('<path d="M22 37 L70 65 L118 37 M70 65 V120 M22 93 L70 65 L118 93" stroke="#fff" stroke-opacity=".45" stroke-width="1.5" fill="none"/>')
    if t.get('shine'):
        s.append('<polygon points="20,60 60,10 78,10 20,82" fill="#fff" opacity=".28"/>')
    s.append(f'</g><polygon points="{INNER}" fill="none" stroke="rgba(255,255,255,.65)" stroke-width="2"/>')
    if t.get('rivets'):
        for p in INNER.split():
            x, y = p.split(',')
            s.append(f'<circle cx="{x}" cy="{y}" r="2.6" fill="#4a2a10" stroke="rgba(255,255,255,.4)" stroke-width=".8"/>')
    s.append(ICONS[t['icon'] if earned else 'lock'])
    s.append(f'<text x="70" y="102" text-anchor="middle" font-family="Montserrat,Arial,sans-serif" font-size="17" font-weight="800" '
             f'fill="#fff" stroke="rgba(0,0,0,.45)" stroke-width="2.5" paint-order="stroke">{badge["need"]}</text>')
    s.append(f'<path d="M10 118 H130 L122 130 L130 142 H10 L18 130Z" fill="url(#r{g})" stroke="rgba(0,0,0,.35)" stroke-width="1"/>')
    if t.get('grain'):
        s.append('<path d="M14 126 H126 M14 136 H126" stroke="#5a3415" stroke-opacity=".35" stroke-width="1.4"/>')
    font = 12.5 if len(title) <= 9 else 10
    s.append(f'<text x="70" y="135" text-anchor="middle" font-family="Montserrat,Arial,sans-serif" font-size="{font}" font-weight="800" '
             f'letter-spacing="1" fill="#fff" stroke="rgba(0,0,0,.4)" stroke-width="2" paint-order="stroke">{title.upper()}</text></svg>')
    return mark_safe(''.join(s))


def tier_for_count(solved):
    """Самый высокий значок за задачи по числу решённых на 10/10 (запись каталога) или None."""
    from .badges import CATALOG
    best = None
    for b in CATALOG:
        if b['kind'] == 'tasks' and solved >= b['need'] and (best is None or b['need'] > best['need']):
            best = b
    return best


def solved_counts(student_ids):
    """{student_id: сколько задач решено на 10/10} — для рамок аватаров в списках."""
    from django.db.models import Count
    from .models import Submission
    rows = (Submission.objects.filter(student_id__in=list(student_ids), is_graded=True, score=10)
            .values('student_id').annotate(n=Count('id')))
    return {r['student_id']: r['n'] for r in rows}
