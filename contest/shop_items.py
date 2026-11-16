"""Правки магазина от админа (ShopItem) поверх каталогов в коде. Кеш на 15 секунд в процессе; при сохранении сбрасывается."""
import time

_cache = {'at': 0.0, 'data': None}
TTL = 15


def invalidate():
    _cache['at'] = 0.0
    _cache['data'] = None


def rows(kind):
    from .models import ShopItem
    now = time.monotonic()
    if _cache['data'] is None or now - _cache['at'] > TTL:
        data = {}
        for r in ShopItem.objects.defer('image'):
            data.setdefault(r.kind, {})[r.key] = r
        _cache['data'], _cache['at'] = data, now
    return _cache['data'].get(kind, {})


def merged(base, kind, key):
    """Элемент каталога с правками админа (цена, названия, скрыт)."""
    item = dict(base)
    item['hidden'] = False
    r = rows(kind).get(key)
    if r:
        if r.price is not None and item.get('kind') == 'paid':
            item['price'] = r.price
        if r.ru:
            item['ru'] = r.ru
        if r.kk:
            item['kk'] = r.kk
        item['hidden'] = bool(r.hidden) and item.get('kind') == 'paid'
    return item
