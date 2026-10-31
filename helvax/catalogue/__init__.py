"""The product catalogue: vaccine trade names and the antigens they cover (catalogue/products-ch.yaml).

Checked against the schedule at startup: every antigen of a product must be one of the schedule's, and every
product tag the schedule's rules refer to (`products`, `if_previous_product`) must be on some product.
"""
import os
from dataclasses import dataclass

import yaml

from ..schedule import ScheduleError

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), 'products-ch.yaml')


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    group: str
    antigens: tuple
    tags: frozenset = frozenset()  # for product-dependent rules, e.g. menveo, pcv13


class Catalogue:
    def __init__(self, products):
        self.products = products
        self.by_key = {p.key: p for p in products}

    def get(self, key):
        return self.by_key.get(key)

    def groups(self):
        """[(group, [products])] in file order, for <optgroup>."""
        out = {}
        for p in self.products:
            out.setdefault(p.group, []).append(p)
        return list(out.items())


def parse(data, schedule):
    if not isinstance(data, dict) or set(data) != {'products'}:
        raise ScheduleError('catalogue: expected a mapping with "products"')
    products, seen = [], set()
    for i, p in enumerate(data['products'] or []):
        where = f'products[{i}]'
        if not isinstance(p, dict):
            raise ScheduleError(f'{where}: expected a mapping')
        unknown = set(p) - {'key', 'name', 'group', 'antigens', 'tags'}
        if unknown or not {'key', 'name', 'group', 'antigens'} <= set(p):
            raise ScheduleError(f'{where}: needs key, name, group, antigens (and optionally tags)')
        if p['key'] in seen:
            raise ScheduleError(f'{where}: duplicate key {p["key"]!r}')
        seen.add(p['key'])
        antigens = tuple(p['antigens'] or ())
        missing = [a for a in antigens if a not in schedule.antigens]
        if not antigens or missing:
            raise ScheduleError(f'{where} ({p["name"]}): unknown antigen(s) {", ".join(missing) or "(none)"}')
        tags = p.get('tags') or []
        if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
            raise ScheduleError(f'{where} ({p["name"]}): tags must be a list of names')
        products.append(Product(str(p['key']), str(p['name']), str(p['group']), antigens, frozenset(tags)))
    known = {t for p in products for t in p.tags}
    missing = sorted(schedule.product_tags - known)
    if missing:
        raise ScheduleError(f'the schedule refers to product tag(s) no product has: {", ".join(missing)}')
    return Catalogue(products)


def load(path, schedule):
    with open(path, encoding='utf-8') as f:
        try:
            return parse(yaml.safe_load(f), schedule)
        except (yaml.YAMLError, ScheduleError) as e:
            raise ScheduleError(f'{path}: {e}') from None
