"""The product catalogue: vaccine trade names and the antigens they cover (catalogue/products-ch.yaml).

Checked against the schedule at startup: every antigen of a product must be one of the schedule's.
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
    valency: int | None = None


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
        unknown = set(p) - {'key', 'name', 'group', 'antigens', 'valency'}
        if unknown or not {'key', 'name', 'group', 'antigens'} <= set(p):
            raise ScheduleError(f'{where}: needs key, name, group, antigens (and optionally valency)')
        if p['key'] in seen:
            raise ScheduleError(f'{where}: duplicate key {p["key"]!r}')
        seen.add(p['key'])
        antigens = tuple(p['antigens'] or ())
        missing = [a for a in antigens if a not in schedule.antigens]
        if not antigens or missing:
            raise ScheduleError(f'{where} ({p["name"]}): unknown antigen(s) {", ".join(missing) or "(none)"}')
        products.append(Product(str(p['key']), str(p['name']), str(p['group']), antigens, p.get('valency')))
    return Catalogue(products)


def load(path, schedule):
    with open(path, encoding='utf-8') as f:
        try:
            return parse(yaml.safe_load(f), schedule)
        except (yaml.YAMLError, ScheduleError) as e:
            raise ScheduleError(f'{path}: {e}') from None
