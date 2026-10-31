"""The vaccination engine: pure functions, no database, no Flask, no clock.

`evaluate(profile, doses, schedule, today)` returns, for every antigen of the schedule, its status, the
next dose (with the earliest, recommended and latest date) and the plan of the doses still missing.
Everything works per antigen: a combination vaccine is one `Dose` per antigen it covers.

Statuses (most urgent first): overdue, due, upcoming, complete, not_recommended. `upcoming` means a
series is in progress (its next dose is in the future), or a dose is recommended within `horizon`.
A rule that only starts later (e.g. HPV at 11) is `not_recommended` with `next` set.

"Overdue" is an app convention: the schedule gives recommended ages and windows; a dose counts as
overdue after its window, or `overdue_grace` (schedule) / `booster_grace` (rule, for boosters) after its
recommended date.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from .dates import add_months

DEFAULT_HORIZON = timedelta(days=90)
STATUSES = ('overdue', 'due', 'upcoming', 'complete', 'not_recommended')
STATUS_LABELS = {'overdue': 'überfällig', 'due': 'fällig', 'upcoming': 'demnächst', 'complete': 'vollständig',
                 'not_recommended': 'nicht empfohlen'}
# Internal: a part that only starts later; reported as not_recommended with `next`
_LATER = 'later'
_RANK = {'overdue': 0, 'due': 1, 'upcoming': 2, 'complete': 3, _LATER: 4, 'not_recommended': 5}
DOCTOR = 'Nachholschema mit Ärztin/Arzt festlegen'


@dataclass(frozen=True)
class Profile:
    birth_date: date
    flags: frozenset = frozenset()
    immunities: frozenset = frozenset()  # antigens with documented immunity (disease, serology)


@dataclass(frozen=True)
class Dose:
    date: date
    antigen: str
    ref: object = None  # e.g. the vaccination entry id; passed through to the result


@dataclass(frozen=True)
class PlannedDose:
    number: int            # dose number of the antigen (1 = first dose ever)
    earliest: date
    recommended: date
    latest: date | None    # overdue after this day; None = no deadline
    asap: bool = False     # "as soon as possible": catch-up without a fixed age
    course: str = ''
    label: str = ''


@dataclass(frozen=True)
class CountedDose:
    date: date
    number: int | None     # None = given, but not counted (e.g. before the minimum age)
    ref: object = None
    warning: str | None = None


@dataclass(frozen=True)
class AntigenStatus:
    antigen: str
    label: str
    category: str
    status: str
    next: PlannedDose | None
    plan: tuple = ()
    doses: tuple = ()
    reason: str | None = None
    needs_doctor: bool = False
    warnings: tuple = ()
    ref: str = ''
    review: bool = False

    @property
    def status_label(self):
        return STATUS_LABELS[self.status]


@dataclass
class _Part:
    status: str
    next: PlannedDose | None = None
    plan: list = field(default_factory=list)
    reason: str | None = None
    needs_doctor: bool = False
    category: str | None = None
    complete: bool = False
    started: bool = True  # False: a course that only starts later (decides which reason is shown)


def _after(d, duration):
    return duration.after(d) if duration else d


def _age_text(duration):
    return duration.label() if duration else ''


def _md_on_or_after(md, day):
    """The next date with (month, day) on or after `day`; 29 Feb becomes 28 Feb in other years."""
    for year in (day.year, day.year + 1):
        try:
            d = date(year, *md)
        except ValueError:
            d = date(year, md[0], 28)
        if d >= day:
            return d
    raise AssertionError('unreachable')


def _status_for(planned, today, horizon, in_progress, catch_up):
    if planned.latest is not None and today > planned.latest:
        return 'overdue'
    if today >= planned.recommended:
        return 'overdue' if planned.asap and catch_up else 'due'
    if in_progress or planned.recommended - today <= horizon:
        return 'upcoming'
    return _LATER


def _choose_series(course, birth, day):
    """The series whose start ages contain `day` (the first dose, or today); in a gap between them
    (e.g. no catch-up between 1 and 10 years) the next one that starts later; after all of them None."""
    for s in course.series:
        if s.start_age.contains(birth, day):
            return s
    later = [s for s in course.series if s.start_age.start.after(birth) > day]
    return min(later, key=lambda s: s.start_age.start.after(birth)) if later else None


def _evaluate_series_course(rule, course, profile, counted, schedule, today, horizon, warnings):
    birth = profile.birth_date
    start = course.ages.start.after(birth)
    end = course.ages.until.after(birth) if course.ages.until else None
    given = [d for d in counted if course.ages.contains(birth, d.date)]
    grace = schedule.overdue_grace
    series = _choose_series(course, birth, given[0].date if given else max(today, start))
    if series is None:
        return _Part('not_recommended', reason=f'Nur bis {_age_text(course.ages.until)} empfohlen')
    category = series.category or course.category or rule.category
    if category == 'supplementary' and f'optin:{rule.antigen}' not in profile.flags:
        return _Part('not_recommended', reason='Ergänzende Impfung, im Profil nicht gewählt', category=category,
                     started=today >= start)

    # Check the doses already given against the series
    prev = None
    for spec, d in zip(series.doses, given, strict=False):
        if spec.min_age and d.date < spec.min_age.after(birth):
            warnings.append(f'Dosis {d.number} vom {d.date:%d.%m.%Y} wurde jünger als {spec.min_age.label()} gegeben.')
        if prev and spec.min_interval and d.date < spec.min_interval.after(prev):
            warnings.append(f'Dosis {d.number} vom {d.date:%d.%m.%Y}: weniger als {spec.min_interval.label()} '
                            f'nach der vorherigen Dosis.')
        prev = d.date
    if len(given) >= len(series.doses):
        return _Part('complete', category=category, complete=True)

    first_number = (given[-1].number if given else len(counted)) + 1
    plan = []
    prev = given[-1].date if given else None  # date of the previous dose: given, or planned (but not before today)
    part = None
    for i, spec in enumerate(series.doses[len(given):]):
        earliest = max(_after(birth, spec.min_age), start)
        if plan:
            earliest = max(earliest, prev)
        if prev and spec.min_interval:
            earliest = max(earliest, spec.min_interval.after(prev))
        if spec.at:
            recommended = spec.at.after(birth)
        elif spec.window:
            recommended = spec.window.start.after(birth)
        elif spec.after and prev:
            recommended = spec.after.after(prev)
        else:
            recommended = None
        asap = recommended is None
        if spec.overdue_after:
            latest = spec.overdue_after.after(birth)
        elif spec.window and spec.window.until:
            latest = spec.window.until.after(birth) - timedelta(days=1)
        elif not asap:
            latest = grace.after(recommended)
        else:
            latest = None
        recommended = max(recommended or today, earliest)
        if latest is not None and latest < recommended:
            latest = None
        limits = [x for x in (spec.until.after(birth) if spec.until else None, end) if x is not None]
        deadline = min(limits) if limits else None  # exclusive
        planned = PlannedDose(first_number + i, earliest, recommended, latest, asap, course.id, spec.label)

        if part is None:
            # The next dose decides the status
            if deadline is not None and max(today, earliest) >= deadline:
                if spec.if_missed == 'doctor':
                    return _Part('overdue', next=planned, reason=DOCTOR, needs_doctor=True, category=category)
                reason = (f'Nur bis {_age_text(spec.until or course.ages.until)} empfohlen' if not given
                          else 'Serie unvollständig; Altersgrenze für weitere Dosen erreicht')
                return _Part('not_recommended', reason=reason, category=category)
            if spec.if_missed == 'doctor' and latest is not None and today > latest:
                return _Part('overdue', next=planned, reason=DOCTOR, needs_doctor=True, category=category)
            status = _status_for(planned, today, horizon, in_progress=bool(given), catch_up=series.catch_up)
            part = _Part(status, next=planned, category=category,
                         reason=f'Empfohlen ab {planned.recommended:%d.%m.%Y}' if status == _LATER else None)
        elif deadline is not None and planned.recommended >= deadline:
            break  # can't be given in time any more; the plan ends here
        plan.append(planned)
        prev = max(planned.recommended, today)
    part.plan = plan
    return part


def _evaluate_birth_month_course(rule, course, profile, counted, today, horizon):
    birth = profile.birth_date
    end = course.ages.until.after(birth) if course.ages.until else None
    if any(course.ages.contains(birth, d.date) for d in counted):
        return _Part('complete', complete=True)
    if end is not None and today >= end:
        return _Part('not_recommended', reason=f'Nur bis {_age_text(course.ages.until)} empfohlen')
    option = next((o for o in course.birth_month if birth.month in o.birth_months), None)
    if option is None:
        return _Part('not_recommended', reason='Für diesen Geburtsmonat nicht vorgesehen')
    if option.at is not None:
        recommended = option.at.after(birth)
        latest = option.latest.after(birth) if option.latest else None
    else:
        recommended = _md_on_or_after(option.on_date, birth)
        latest = _md_on_or_after(option.latest_date, recommended) if option.latest_date else None
    if option.missed_after:
        missed = _md_on_or_after(option.missed_after, birth)
        if today > missed:
            recommended = _md_on_or_after(option.missed_then, missed)
            latest = None
    if end is not None and recommended >= end:
        return _Part('not_recommended', reason=f'Nur bis {_age_text(course.ages.until)} empfohlen')
    number = len(counted) + 1
    planned = PlannedDose(number, birth, recommended, latest, False, course.id)
    status = _status_for(planned, today, horizon, in_progress=False, catch_up=False)
    return _Part(status, next=planned, plan=[planned],
                 reason=f'Empfohlen ab {recommended:%d.%m.%Y}' if status == _LATER else None)


def _evaluate_recurring(rule, profile, counted, schedule, today, horizon):
    birth = profile.birth_date
    last = counted[-1].date
    step = next((s for s in rule.recurring if s.age_at_last.contains(birth, last)), None)
    if step is None:
        return None
    if step.interval:
        recommended = step.interval.after(last)
    else:
        recommended = step.next_at_age.after(birth)
        if recommended <= last:
            return None
    grace = rule.booster_grace or schedule.overdue_grace
    planned = PlannedDose(len(counted) + 1, recommended, recommended, grace.after(recommended), False, 'booster',
                          'Auffrischimpfung')
    status = _status_for(planned, today, horizon, in_progress=False, catch_up=False)
    return _Part('complete' if status == _LATER else status, next=planned, plan=[planned], complete=True)


def _evaluate_seasonal(rule, profile, counted, today, horizon):
    s = rule.seasonal
    birth = profile.birth_date
    season_start = _md_on_or_after(s.counts_from, date(today.year, 1, 1))
    if today < season_start:
        season_start = _md_on_or_after(s.counts_from, date(today.year - 1, 1, 1))
    eligible_from = s.from_age.after(birth)
    covered = any(d.date >= season_start for d in counted)

    def window(start_day):
        a = _md_on_or_after(s.recommend[0], start_day)
        return a, _md_on_or_after(s.recommend[1], a)

    w_start, w_end = window(season_start)
    if covered or eligible_from > w_end:
        next_season = _md_on_or_after(s.counts_from, season_start + timedelta(days=1))
        w_start, w_end = window(max(next_season, eligible_from))
    recommended = max(w_start, eligible_from)
    if s.min_interval and counted:
        recommended = max(recommended, s.min_interval.after(counted[-1].date))
    planned = PlannedDose(len(counted) + 1, recommended, recommended, w_end, False, 'season', 'Saisonale Impfung')
    status = _status_for(planned, today, horizon, in_progress=False, catch_up=False)
    if status == _LATER:
        status = 'complete' if counted else _LATER
    reason = f'Empfohlen ab {recommended:%d.%m.%Y}' if status == _LATER else None
    return _Part(status, next=planned, plan=[planned], reason=reason, complete=covered)


def _flag_label(schedule, key):
    flag = schedule.flags.get(key)
    return flag.label if flag else key


def evaluate_rule(rule, profile, doses, schedule, today, horizon=DEFAULT_HORIZON):
    """AntigenStatus of one rule. `doses` are the Dose objects of this antigen (any order)."""
    antigen = schedule.antigens[rule.antigen]
    birth = profile.birth_date
    doses = sorted(doses, key=lambda d: d.date)
    counted, number, warnings = [], 0, []
    for d in doses:
        if rule.not_counted_before and d.date < rule.not_counted_before.after(birth):
            counted.append(CountedDose(d.date, None, d.ref,
                                       f'Zählt nicht: jünger als {rule.not_counted_before.label()} gegeben.'))
            continue
        number += 1
        counted.append(CountedDose(d.date, number, d.ref))
    valid = [c for c in counted if c.number is not None]
    warnings += [c.warning for c in counted if c.warning]

    def result(part, category=None):
        status = 'not_recommended' if part.status == _LATER else part.status
        return AntigenStatus(rule.antigen, antigen.label, category or part.category or rule.category, status,
                             part.next, tuple(part.plan), tuple(counted), part.reason, part.needs_doctor,
                             tuple(warnings), rule.ref, rule.review)

    if rule.antigen in profile.immunities:
        return result(_Part('not_recommended', reason='Immun (durchgemachte Krankheit oder Serologie)'))
    if rule.exempt_if_born_before and birth < rule.exempt_if_born_before:
        return result(_Part('not_recommended',
                            reason=f'Vor {rule.exempt_if_born_before.year} geboren: gilt als immun'))
    if rule.requires_flag and rule.requires_flag not in profile.flags:
        return result(_Part('not_recommended', reason=f'Nur bei: {_flag_label(schedule, rule.requires_flag)}'))
    if rule.skip_if_flag and rule.skip_if_flag in profile.flags:
        return result(_Part('not_recommended', reason=f'Nicht nötig: {_flag_label(schedule, rule.skip_if_flag)}'))
    if today < birth:
        return result(_Part('not_recommended', reason='Noch nicht geboren'))

    parts = []
    for course in rule.courses:
        if course.birth_month:
            parts.append(_evaluate_birth_month_course(rule, course, profile, valid, today, horizon))
        else:
            parts.append(_evaluate_series_course(rule, course, profile, valid, schedule, today, horizon, warnings))
    if rule.recurring and valid:
        # Boosters once every course that applies is through (complete, or no longer recommended)
        pending = [p for p in parts if p.status in ('overdue', 'due', 'upcoming')]
        if not pending and any(p.complete for p in parts):
            booster = _evaluate_recurring(rule, profile, valid, schedule, today, horizon)
            if booster:
                parts = [p for p in parts if p.status != _LATER or p.next is None] + [booster]
    if rule.seasonal:
        if rule.category == 'supplementary' and f'optin:{rule.antigen}' not in profile.flags:
            parts.append(_Part('not_recommended', reason='Ergänzende Impfung, im Profil nicht gewählt'))
        else:
            parts.append(_evaluate_seasonal(rule, profile, valid, today, horizon))

    # Most urgent first; among parts that are all over, the last one that has started (e.g. "only until 20"
    # rather than "until 5", but not the opt-in of a course that starts at 65)
    best = min(reversed(parts), key=lambda p: (_RANK[p.status], p.next.recommended if p.next else date.max,
                                               not p.started))
    if best.status == 'not_recommended' and any(p.complete for p in parts):
        best = next(p for p in parts if p.complete)
    plan = sorted((d for p in parts if p.status != 'not_recommended' for d in p.plan),
                  key=lambda d: d.recommended)
    if best.status == 'complete' and best.next is None:
        # The soonest future dose of a later part (e.g. the adolescent course after the infant one)
        later = [p.next for p in parts if p.next is not None]
        if later:
            best = _Part('complete', next=min(later, key=lambda d: d.recommended), category=best.category,
                         complete=True)
    best.plan = plan
    return result(best)


def evaluate(profile, doses, schedule, today, horizon=DEFAULT_HORIZON):
    """AntigenStatus for every rule of the schedule, in the schedule's order."""
    by_antigen = {}
    for d in doses:
        by_antigen.setdefault(d.antigen, []).append(d)
    return [evaluate_rule(rule, profile, by_antigen.get(antigen, []), schedule, today, horizon)
            for antigen, rule in schedule.rules.items()]


def number_doses(profile, doses, schedule):
    """{(ref, antigen): dose number or None (not counted)} for display in the vaccination record."""
    numbers = {}
    by_antigen = {}
    for d in doses:
        by_antigen.setdefault(d.antigen, []).append(d)
    for antigen, items in by_antigen.items():
        rule = schedule.rules.get(antigen)
        n = 0
        for d in sorted(items, key=lambda d: d.date):
            if rule and rule.not_counted_before and d.date < rule.not_counted_before.after(profile.birth_date):
                numbers[(d.ref, antigen)] = None
                continue
            n += 1
            numbers[(d.ref, antigen)] = n
    return numbers


def summary(statuses):
    """The most urgent status of a profile (for the dashboard) and the statuses that need action."""
    actionable = [s for s in statuses if s.status in ('overdue', 'due', 'upcoming')]
    actionable.sort(key=lambda s: (_RANK[s.status], s.next.recommended if s.next else date.max))
    return actionable


__all__ = ['Profile', 'Dose', 'PlannedDose', 'CountedDose', 'AntigenStatus', 'evaluate', 'evaluate_rule',
           'number_doses', 'summary', 'STATUSES', 'STATUS_LABELS', 'DEFAULT_HORIZON', 'add_months']
