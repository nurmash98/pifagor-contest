from django import template

from django.templatetags.static import static

from .. import avatars
from django.utils.html import format_html

from ..badge_art import rank_svg, tier_for_count, tier_svg

register = template.Library()


@register.simple_tag
def tier_badge(b, uid='x', earned=True, width=None, height=None):
    """SVG значка за задачи в материале его уровня (дерево, бронза, …)."""
    return tier_svg(b, uid=uid, earned=bool(earned), width=width, height=height)


@register.inclusion_tag('avatar.html', takes_context=True)
def student_avatar(context, solved=0, size=90, mini=False, uid='a', avatar=''):
    """Аватар ученика (картинка или 🎓 по умолчанию) с рамкой из материала самого высокого значка."""
    tier = tier_for_count(solved or 0)
    pic = avatars.get(avatar)
    lang = context.get('content_lang', 'ru')
    data = None
    if tier is not None:
        data = dict(tier, title=tier['kk'] if lang == 'kk' else tier['ru'])
    return {'tier': data, 'size': size, 'img': static(pic['file']) if pic else '', 'mini': mini and data is not None, 'uid': uid,
            'emoji_size': int(size * 0.42), 'mini_w': int(size * 0.42), 'mini_h': int(size * 0.47)}


@register.inclusion_tag('name_banner.html')
def student_name(student, small=True, name=None):
    """Имя ученика с баннером под ним (если ученик его поставил)."""
    from ..banners import clean_custom, custom_style
    raw = getattr(student, 'custom_banner', None) or {}
    return {'name': name or getattr(student, 'full_name', ''), 'banner': getattr(student, 'name_banner', ''), 'small': small,
            'custom': clean_custom(raw), 'custom_style': custom_style(raw)}


@register.simple_tag
def rank_chip(solved, uid='r', size='md'):
    """Плашка ранга рядом с именем: Intern / Junior / Middle / Senior / Team Lead / Architect."""
    tier = tier_for_count(solved or 0)
    if tier is None:
        return ''
    from ..badge_art import TIERS
    return format_html('<span class="rank-chip rk-{} rk-{}" title="{} · {}+ ✓">{}<span class="rk-t">{}</span></span>',
                       TIERS[tier['key']]['tier'], size, tier['ru'], tier['need'],
                       rank_svg(tier, uid=uid, size=16 if size == 'sm' else 20), tier['ru'])
