"""Loading and validating a vaccination schedule file (helvax/schedules/*.yaml).

The file is data, not code: changing it changes what the engine recommends. `load()` checks it
strictly (unknown keys, antigens or flags are errors, so a typo can't silently drop a rule) and
returns frozen dataclasses. Format: see docs/DEVELOPMENT.md, "Schedule file".

Ages and intervals are calendar durations ('6w', '12m', '25y', '14d', see dates.Duration). Age ranges
are half-open: [from, until), with `null` for "no limit". So `[4y, 8y]` means "from the 4th birthday
to the day before the 8th birthday", like the Swiss schedule's "4–7 Jahre".
"""
from dataclasses import dataclass
from datetime import date

import yaml

from .dates import Duration

CATEGORIES = {
    'basic': 'Basisimpfung',
    'supplementary': 'Ergänzende Impfung',
    'risk_group': 'Risikogruppe',
    'travel': 'Reiseimpfung',
}
# Computed by the engine; risk_group and travel rules are modelled, but only evaluated when the profile
# has the rule's `requires_flag` (e.g. FSME), otherwise listed as not recommended.
IF_MISSED = ('overdue', 'skip', 'doctor')


class ScheduleError(ValueError):
    """The schedule file is invalid; the message names the place."""


@dataclass(frozen=True)
class AgeRange:
    start: Duration
    until: Duration | None  # exclusive; None = no upper limit

    def contains(self, birth, day):
        return self.start.after(birth) <= day and (self.until is None or day < self.until.after(birth))


@dataclass(frozen=True)
class DoseSpec:
    at: Duration | None = None            # recommended age
    window: AgeRange | None = None        # recommended age range (start = recommended, end = overdue)
    after: Duration | None = None         # recommended interval after the previous dose
    min_age: Duration | None = None
    min_interval: Duration | None = None  # since the previous dose
    overdue_after: Duration | None = None  # age; default: end of window, or recommended + grace
    until: Duration | None = None         # not given from this age on
    if_missed: str = 'overdue'            # when `until`/the window passed: overdue | skip (series ends) | doctor
    label: str = ''


@dataclass(frozen=True)
class Series:
    start_age: AgeRange                   # chosen by the age at the first dose of the course (or today)
    doses: tuple[DoseSpec, ...]
    catch_up: bool = False                # doses "as soon as possible" are already overdue
    category: str | None = None           # overrides the rule's category for this series
    note: str = ''


@dataclass(frozen=True)
class BirthMonthOption:
    """RSV-style single dose whose timing depends on the birth month."""
    birth_months: tuple[int, ...]
    at: Duration | None = None            # age at which it is recommended ...
    latest: Duration | None = None        # ... and the age after which it is overdue
    on_date: tuple[int, int] | None = None     # or (month, day): the next one after birth
    latest_date: tuple[int, int] | None = None  # (month, day) after which it is overdue
    missed_after: tuple[int, int] | None = None  # not given by this (month, day) after birth: ...
    missed_then: tuple[int, int] | None = None   # ... recommended again on this (month, day)


@dataclass(frozen=True)
class Course:
    """An independent part of a rule, e.g. the infant and the adolescent meningococcal vaccination.
    A dose belongs to the course whose `ages` contain the age at which it was given."""
    id: str
    ages: AgeRange
    series: tuple[Series, ...] = ()
    birth_month: tuple[BirthMonthOption, ...] = ()
    category: str | None = None
    note: str = ''


@dataclass(frozen=True)
class RecurringStep:
    age_at_last: AgeRange                 # age at the last dose decides ...
    interval: Duration | None = None      # ... the interval to the next one
    next_at_age: Duration | None = None   # ... or a fixed age for the next one


@dataclass(frozen=True)
class Seasonal:
    from_age: Duration
    recommend: tuple[tuple[int, int], tuple[int, int]]  # (month, day) window, e.g. mid-October to December
    counts_from: tuple[int, int]          # a dose given since this day of the season year covers the season
    min_interval: Duration | None = None  # since the last dose


@dataclass(frozen=True)
class Rule:
    antigen: str
    category: str
    ref: str
    courses: tuple[Course, ...] = ()
    recurring: tuple[RecurringStep, ...] = ()
    seasonal: Seasonal | None = None
    requires_flag: str | None = None
    skip_if_flag: str | None = None
    exempt_if_born_before: date | None = None
    not_counted_before: Duration | None = None  # doses given younger than this don't count
    booster_grace: Duration | None = None  # overdue this long after a booster's date (default: schedule's)
    note: str = ''
    review: bool = False                  # interpretation of the source is uncertain, see OPEN_QUESTIONS.md


@dataclass(frozen=True)
class Flag:
    key: str
    label: str
    kind: str  # 'condition' (a fact about the person) | 'optin' (generated for supplementary rules)


@dataclass(frozen=True)
class Antigen:
    key: str
    label: str
    short: str


@dataclass(frozen=True)
class Schedule:
    id: str
    title: str
    publisher: str
    published: date
    revised: date | None
    source_url: str
    placeholder: bool
    overdue_grace: Duration
    antigens: dict
    flags: dict
    rules: dict  # antigen -> Rule

    @property
    def version(self):
        """Shown in the UI, e.g. 'ch-2026 (Stand 16.04.2026)'."""
        d = self.revised or self.published
        return f'{self.id} (Stand {d:%d.%m.%Y})'

    def optin_flags(self, rule):
        """The opt-in flags a profile needs for this rule's supplementary parts."""
        return {f'optin:{rule.antigen}'} if self.has_supplementary(rule) else set()

    def has_supplementary(self, rule):
        cats = {rule.category} | {c.category for c in rule.courses if c.category} \
            | {s.category for c in rule.courses for s in c.series if s.category}
        return 'supplementary' in cats


# --- Parsing -------------------------------------------------------------------------------------

def _keys(where, data, allowed, required=()):
    if not isinstance(data, dict):
        raise ScheduleError(f'{where}: expected a mapping')
    unknown = set(data) - set(allowed)
    if unknown:
        raise ScheduleError(f'{where}: unknown key(s) {", ".join(sorted(unknown))}')
    missing = [k for k in required if k not in data]
    if missing:
        raise ScheduleError(f'{where}: missing {", ".join(missing)}')


def _dur(where, value, allow_none=True):
    if value is None:
        if allow_none:
            return None
        raise ScheduleError(f'{where}: a duration is required')
    try:
        return Duration.parse(value)
    except ValueError as e:
        raise ScheduleError(f'{where}: {e}') from None


def _range(where, value):
    if not (isinstance(value, list) and len(value) == 2):
        raise ScheduleError(f'{where}: expected [from, until]')
    return AgeRange(_dur(where, value[0], allow_none=False), _dur(where, value[1]))


def _month_day(where, value):
    try:
        month, day = (int(x) for x in str(value).split('-'))
        date(2000, month, day)  # leap year, so 02-29 is accepted
    except ValueError:
        raise ScheduleError(f'{where}: expected MM-DD, got {value!r}') from None
    return month, day


def _date(where, value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ScheduleError(f'{where}: expected a date YYYY-MM-DD, got {value!r}') from None


def _dose(where, d):
    _keys(where, d, ['at', 'window', 'after', 'min_age', 'min_interval', 'overdue_after', 'until', 'if_missed',
                     'label'])
    timing = [k for k in ('at', 'window', 'after') if d.get(k) is not None]
    if len(timing) > 1:
        raise ScheduleError(f'{where}: use only one of at, window, after')
    if d.get('if_missed', 'overdue') not in IF_MISSED:
        raise ScheduleError(f'{where}: if_missed must be one of {", ".join(IF_MISSED)}')
    return DoseSpec(at=_dur(where, d.get('at')), window=_range(where, d['window']) if d.get('window') else None,
                    after=_dur(where, d.get('after')), min_age=_dur(where, d.get('min_age')),
                    min_interval=_dur(where, d.get('min_interval')),
                    overdue_after=_dur(where, d.get('overdue_after')), until=_dur(where, d.get('until')),
                    if_missed=d.get('if_missed', 'overdue'), label=str(d.get('label', '')))


def _series(where, s):
    _keys(where, s, ['start_age', 'doses', 'catch_up', 'category', 'note'], ['start_age', 'doses'])
    if s.get('category') is not None and s['category'] not in CATEGORIES:
        raise ScheduleError(f'{where}: unknown category {s["category"]!r}')
    if not s['doses']:
        raise ScheduleError(f'{where}: a series needs at least one dose')
    return Series(start_age=_range(f'{where}.start_age', s['start_age']),
                  doses=tuple(_dose(f'{where}.doses[{i}]', d) for i, d in enumerate(s['doses'])),
                  catch_up=bool(s.get('catch_up', False)), category=s.get('category'), note=str(s.get('note', '')))


def _birth_month(where, o):
    _keys(where, o, ['birth_months', 'at', 'latest', 'on_date', 'latest_date', 'missed_after', 'missed_then'],
          ['birth_months'])
    months = tuple(o['birth_months'])
    if not months or any(not isinstance(m, int) or not 1 <= m <= 12 for m in months):
        raise ScheduleError(f'{where}: birth_months must be month numbers 1–12')
    if (o.get('at') is None) == (o.get('on_date') is None):
        raise ScheduleError(f'{where}: use one of at, on_date')
    md = {k: _month_day(f'{where}.{k}', o[k]) if o.get(k) is not None else None
          for k in ('on_date', 'latest_date', 'missed_after', 'missed_then')}
    return BirthMonthOption(birth_months=months, at=_dur(where, o.get('at')), latest=_dur(where, o.get('latest')), **md)


def _course(where, c, index):
    _keys(where, c, ['id', 'ages', 'series', 'birth_month', 'category', 'note'], ['ages'])
    if c.get('category') is not None and c['category'] not in CATEGORIES:
        raise ScheduleError(f'{where}: unknown category {c["category"]!r}')
    series = tuple(_series(f'{where}.series[{i}]', s) for i, s in enumerate(c.get('series') or []))
    birth_month = tuple(_birth_month(f'{where}.birth_month[{i}]', o) for i, o in enumerate(c.get('birth_month') or []))
    if bool(series) == bool(birth_month):
        raise ScheduleError(f'{where}: a course needs either series or birth_month')
    return Course(id=str(c.get('id', f'course{index + 1}')), ages=_range(f'{where}.ages', c['ages']),
                  series=series, birth_month=birth_month, category=c.get('category'), note=str(c.get('note', '')))


def _rule(where, r, antigens, flags):
    _keys(where, r, ['antigen', 'category', 'ref', 'courses', 'recurring', 'seasonal', 'requires_flag',
                     'skip_if_flag', 'exempt_if_born_before', 'not_counted_before', 'booster_grace', 'note',
                     'review'], ['antigen', 'category', 'ref'])
    if r['antigen'] not in antigens:
        raise ScheduleError(f'{where}: unknown antigen {r["antigen"]!r}')
    if r['category'] not in CATEGORIES:
        raise ScheduleError(f'{where}: unknown category {r["category"]!r}')
    for key in ('requires_flag', 'skip_if_flag'):
        if r.get(key) is not None and r[key] not in flags:
            raise ScheduleError(f'{where}: {key} {r[key]!r} is not declared under flags')
    recurring = []
    for i, step in enumerate(r.get('recurring') or []):
        w = f'{where}.recurring[{i}]'
        _keys(w, step, ['age_at_last', 'interval', 'next_at_age'], ['age_at_last'])
        if (step.get('interval') is None) == (step.get('next_at_age') is None):
            raise ScheduleError(f'{w}: use one of interval, next_at_age')
        recurring.append(RecurringStep(_range(f'{w}.age_at_last', step['age_at_last']), _dur(w, step.get('interval')),
                                       _dur(w, step.get('next_at_age'))))
    seasonal = None
    if r.get('seasonal') is not None:
        s, w = r['seasonal'], f'{where}.seasonal'
        _keys(w, s, ['from_age', 'recommend', 'counts_from', 'min_interval'], ['from_age', 'recommend', 'counts_from'])
        if not (isinstance(s['recommend'], list) and len(s['recommend']) == 2):
            raise ScheduleError(f'{w}.recommend: expected [MM-DD, MM-DD]')
        seasonal = Seasonal(_dur(w, s['from_age'], allow_none=False),
                            tuple(_month_day(f'{w}.recommend', x) for x in s['recommend']),
                            _month_day(f'{w}.counts_from', s['counts_from']), _dur(w, s.get('min_interval')))
    courses = tuple(_course(f'{where}.courses[{i}]', c, i) for i, c in enumerate(r.get('courses') or []))
    if not courses and not seasonal:
        raise ScheduleError(f'{where}: a rule needs courses or seasonal')
    return Rule(antigen=r['antigen'], category=r['category'], ref=str(r['ref']), courses=courses,
                recurring=tuple(recurring), seasonal=seasonal, requires_flag=r.get('requires_flag'),
                skip_if_flag=r.get('skip_if_flag'),
                exempt_if_born_before=_date(where, r['exempt_if_born_before'])
                if r.get('exempt_if_born_before') else None,
                not_counted_before=_dur(where, r.get('not_counted_before')),
                booster_grace=_dur(where, r.get('booster_grace')), note=str(r.get('note', '')),
                review=bool(r.get('review', False)))


def parse(data):
    """A Schedule from the parsed YAML document."""
    _keys('file', data, ['schedule', 'antigens', 'flags', 'rules'], ['schedule', 'antigens', 'rules'])
    head = data['schedule']
    _keys('schedule', head, ['id', 'title', 'publisher', 'published', 'revised', 'source_url', 'placeholder',
                             'overdue_grace'], ['id', 'title', 'publisher', 'published', 'source_url'])
    antigens = {}
    for key, a in (data['antigens'] or {}).items():
        _keys(f'antigens.{key}', a, ['label', 'short'], ['label', 'short'])
        antigens[key] = Antigen(key, str(a['label']), str(a['short']))
    flags = {}
    for key, f in (data.get('flags') or {}).items():
        _keys(f'flags.{key}', f, ['label'], ['label'])
        if key.startswith('optin:'):
            raise ScheduleError(f'flags.{key}: opt-in flags are generated, don\'t declare them')
        flags[key] = Flag(key, str(f['label']), 'condition')
    rules = {}
    for i, r in enumerate(data['rules'] or []):
        rule = _rule(f'rules[{i}] ({r.get("antigen", "?") if isinstance(r, dict) else "?"})', r, antigens, flags)
        if rule.antigen in rules:
            raise ScheduleError(f'rules[{i}]: a second rule for {rule.antigen!r}')
        rules[rule.antigen] = rule
    schedule = Schedule(id=str(head['id']), title=str(head['title']), publisher=str(head['publisher']),
                        published=_date('schedule.published', head['published']),
                        revised=_date('schedule.revised', head['revised']) if head.get('revised') else None,
                        source_url=str(head['source_url']), placeholder=bool(head.get('placeholder', False)),
                        overdue_grace=_dur('schedule.overdue_grace', head.get('overdue_grace', '1m'),
                                           allow_none=False),
                        antigens=antigens, flags=flags, rules=rules)
    for rule in rules.values():
        for key in schedule.optin_flags(rule):
            flags[key] = Flag(key, f'{antigens[rule.antigen].label} (ergänzende Impfung)', 'optin')
    return schedule


def load(path):
    with open(path, encoding='utf-8') as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ScheduleError(f'{path}: {e}') from None
    try:
        return parse(data)
    except ScheduleError as e:
        raise ScheduleError(f'{path}: {e}') from None
