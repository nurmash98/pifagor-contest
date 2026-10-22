from django import template

from ..badge_art import tier_for_count, tier_svg

register = template.Library()


@register.simple_tag
def tier_badge(b, uid='x', earned=True, width=None, height=None):
    """SVG значка за задачи в материале его уровня (дерево, бронза, …)."""
    return tier_svg(b, uid=uid, earned=bool(earned), width=width, height=height)


@register.inclusion_tag('avatar.html', takes_context=True)
def student_avatar(context, solved=0, size=90, mini=False, uid='a'):
    """Аватар ученика с рамкой из материала самого высокого значка за задачи."""
    tier = tier_for_count(solved or 0)
    lang = context.get('content_lang', 'ru')
    data = None
    if tier is not None:
        data = dict(tier, title=tier['kk'] if lang == 'kk' else tier['ru'])
    return {'tier': data, 'size': size, 'mini': mini and data is not None, 'uid': uid,
            'emoji_size': int(size * 0.42), 'mini_w': int(size * 0.42), 'mini_h': int(size * 0.47)}
