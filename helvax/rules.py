"""The schedule and the product catalogue of the running app, loaded once at startup.

`create_app()` calls `init_app()`, which fails with a clear message if a file is invalid, so a broken
rule file never reaches the users. Views get them with `rules.schedule()` and `rules.catalogue()`.
"""
from flask import current_app

from . import catalogue as catalogue_module
from . import schedule as schedule_module


def init_app(app):
    sched = schedule_module.load(app.config['SCHEDULE_FILE'])
    app.extensions['helvax.schedule'] = sched
    app.extensions['helvax.catalogue'] = catalogue_module.load(app.config['CATALOGUE_FILE'], sched)
    if sched.placeholder:
        app.logger.warning('The schedule %s is a placeholder, not real medical rules', sched.id)


def schedule():
    return current_app.extensions['helvax.schedule']


def catalogue():
    return current_app.extensions['helvax.catalogue']
