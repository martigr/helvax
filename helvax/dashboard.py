"""The family dashboard: what is due next across all profiles of the user's family."""
from flask import Blueprint, render_template

from .auth import login_required

bp = Blueprint('dashboard', __name__)


@bp.route('/')
@login_required
def index():
    return render_template('dashboard.html')
