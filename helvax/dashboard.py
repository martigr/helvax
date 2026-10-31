"""The family dashboard (what is due next across all profiles) and the schedule page (/schedule)."""
from flask import Blueprint, g, render_template

from . import dates, engine, profiles, rules
from .auth import login_required
from .schedule import CATEGORIES

bp = Blueprint('dashboard', __name__)


@bp.route('/')
@login_required
def index():
    today = dates.today()
    cards, upcoming = [], []
    for p in profiles.family_profiles():
        statuses, _, record = profiles.evaluate(p)
        actionable = engine.summary(statuses)
        counts = {s: sum(1 for x in statuses if x.status == s) for s in engine.STATUSES}
        birth = dates.parse_day(p['birth_date'])
        cards.append({'profile': p, 'age': dates.age_label(birth, today), 'counts': counts,
                      'worst': actionable[0].status if actionable else 'complete',
                      'entries': len(record[0]), 'doctor': any(s.needs_doctor for s in statuses)})
        upcoming += [{'profile': p, 'status': s} for s in actionable]
    order = {s: i for i, s in enumerate(engine.STATUSES)}
    upcoming.sort(key=lambda u: (order[u['status'].status], u['status'].next.recommended))
    return render_template('dashboard.html', cards=cards, upcoming=upcoming, today=today,
                           has_family=g.user['family_id'] is not None)


@bp.route('/schedule')
@login_required
def schedule_page():
    schedule = rules.schedule()
    return render_template('schedule.html', schedule=schedule, rules=list(schedule.rules.values()),
                           categories=CATEGORIES)
