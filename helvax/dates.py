"""The clock, age arithmetic and German date formatting.

`now()` and `today()` are the only clock of the app; tests replace `dates.now`. Call them as
`dates.today()` (module attribute), never `from .dates import today` and never `date.today()`.
The engine itself never reads the clock: it gets `today` as a parameter.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
          'August', 'September', 'Oktober', 'November', 'Dezember']

# Dates accepted from requests; far outside, the arithmetic would overflow or make no sense
MIN_DAY, MAX_DAY = date(1900, 1, 1), date(2999, 12, 31)


def now():
    """Current local time (naive) in the configured TIMEZONE."""
    return datetime.now(ZoneInfo(current_app.config['TIMEZONE'])).replace(tzinfo=None)


def today():
    return now().date()


def add_months(d, months):
    """`d` plus whole calendar months; the day is clamped to the end of a shorter month
    (31 Jan + 1 month = 28/29 Feb)."""
    total = d.year * 12 + d.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


DURATION_RE = re.compile(r'^(\d+)([dwmy])$')


@dataclass(frozen=True)
class Duration:
    """A calendar duration like '6w', '12m', '25y' or '14d'."""
    n: int
    unit: str  # d | w | m | y

    @classmethod
    def parse(cls, text):
        m = DURATION_RE.match(str(text).strip())
        if not m:
            raise ValueError(f'invalid duration {text!r} (expected e.g. 14d, 6w, 12m, 25y)')
        return cls(int(m.group(1)), m.group(2))

    def after(self, d):
        """The date this duration after `d`."""
        if self.unit == 'd':
            return d + timedelta(days=self.n)
        if self.unit == 'w':
            return d + timedelta(weeks=self.n)
        return add_months(d, self.n * (12 if self.unit == 'y' else 1))

    def __repr__(self):
        return f'{self.n}{self.unit}'

    def label(self):
        """German, e.g. '12 Monate', '1 Jahr'."""
        names = {'d': ('Tag', 'Tage'), 'w': ('Woche', 'Wochen'), 'm': ('Monat', 'Monate'), 'y': ('Jahr', 'Jahre')}
        one, many = names[self.unit]
        return f'{self.n} {one if self.n == 1 else many}'


def age_years(birth, on):
    """Completed years of life on the day `on`."""
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def age_label(birth, on):
    """German age, e.g. '3 Wochen', '7 Monate', '4 Jahre'."""
    if on < birth:
        return 'noch nicht geboren'
    years = age_years(birth, on)
    if years >= 2:
        return f'{years} Jahre'
    months = (on.year - birth.year) * 12 + on.month - birth.month - (on.day < birth.day)
    if months >= 2:
        return f'{months} Monate'
    weeks = (on - birth).days // 7
    return f'{weeks} Woche' if weeks == 1 else f'{weeks} Wochen'


def parse_day(value):
    """The date of an ISO string, or None if it isn't one or lies outside MIN_DAY..MAX_DAY."""
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return day if MIN_DAY <= day <= MAX_DAY else None


def fmt_date(d):
    """Swiss short date: 05.03.2026."""
    return f'{d:%d.%m.%Y}' if d else ''


def fmt_month(d):
    """'März 2026'."""
    return f'{MONTHS[d.month - 1]} {d.year}'
