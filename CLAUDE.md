# CLAUDE.md

Flask + SQLite app that keeps the vaccination records of a family and evaluates them against the Swiss
vaccination schedule. Full developer guide: `docs/DEVELOPMENT.md`. Read it before changing the data model,
the rule format, the engine or security code. The app stores health data.

## Commands

```bash
pip install -r requirements-dev.txt          # setup (use a venv)
ruff check . && pytest                       # both must pass before committing (CI runs the same)
docker build -t helvax:ci . && scripts/docker-smoke-test.sh   # after Dockerfile/entrypoint changes
flask --app helvax --debug seed-demo         # fictional family: anna/password123 (admin), ben/password456
flask --app helvax --debug run               # dev server on :5000
```

## Layout

- `helvax/`: the app package. `create_app()` in `__init__.py`; blueprints `auth`, `dashboard` (also `/schedule`),
  `profiles` (profiles, vaccination entries, immunities, print view), `account`, `admin`, `pwa`.
  Data access in `db.py` and `users.py`; clock and age arithmetic in `dates.py`; CLI in `cli.py`; demo data in
  `demo.py`; security headers in `security.py`; audit log in `audit.py`; outbox and reminder scan in `notifications.py`;
  Web Push in `push.py`.
  `__version__` in `__init__.py` must match `pyproject.toml`.
- **Rules:** `schedule.py` loads and validates `schedules/ch-2026.yaml`; `engine.py` evaluates a profile
  (pure functions); `catalogue/` maps trade names to antigens; `rules.py` holds both for the running app.
- `helvax/templates/`: Jinja templates extending `base.html`; status macros in `_status.html`.
- `helvax/static/style.css`: all styling; colours are CSS variables in `:root`; print styles at the end.
- `tests/`: pytest, one file per area; fixtures in `conftest.py` (`app`, `client`, `login`, `csrf`, `query`,
  `mailbox`, `pushbox`). Engine tests use the made-up `tests/data/test-schedule.yaml`; `test_schedule_ch.py` tests the
  real rules.

## Rules

- UI text is **German** (Swiss style: "ss" instead of "ß", «» quotes). Code, comments, docs and commits are English,
  and so are routes, query parameters and their values, and `#fragment` ids (`?view=table`, `#record`).
- **Don't invent medical rules.** Every rule in `ch-2026.yaml` needs a `ref` to the PDF in `docs/sources/`.
  Anything interpreted gets `review: true` and an entry in `OPEN_QUESTIONS.md` (a test checks this).
  App conventions (e.g. when a dose counts as overdue) are documented there too.
- The engine is pure: no DB, no Flask, no clock (`today` is a parameter). Everything works per antigen.
  Medical logic belongs in the YAML data where possible; add an engine feature only when the format can't
  express a rule, with a test against `tests/data/test-schedule.yaml`.
- Get the current time only via `dates.now()` / `dates.today()` (module attribute; tests replace `dates.now`).
- Family access: load profiles and entries only through `profiles.get_family_profile()` /
  `get_family_vaccination()` (404 for other families). Admins see no health data of other families.
- **No health details** in logs, audit `detail`, mails, push messages or URLs: name the profile, never the
  vaccine, antigen or disease. Tokens never in logs (`accesslog.py` redacts them).
- DB access through `get_db()`; writes inside `with get_db() as con:`. Create users only via `users.create_user()`,
  profiles only via `profiles.create_profile()` (adds the reminder watches), vaccination entries only via
  `profiles.create_entry()`.
- Every POST form needs `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Log every change with `audit.record(con, action, detail)` in the view's transaction; new actions go into
  `audit.ACTIONS`.
- Mail only through the outbox (`notifications.queue_*()`, unique `dedupe_key`); never call `mail.deliver()`
  from a view. Mail only to confirmed addresses.
- No inline `<script>` or `on*=` handlers (CSP); JS goes into `static/*.js` and must be optional.
- Dependency-light: no ORM, no JS framework, no build step, no CDNs or external fonts; pins in `requirements*.txt`.
- Schema lives in `db.SCHEMA`/`init_db()`. After the first release, new columns go into `db.ADDED_COLUMNS`,
  with a test that builds the old schema.
- The repository is public: no secrets, real names, addresses, hostnames, IPs or real vaccination data.
  Demo and test data are fictional; screenshots only from `seed-demo`.
- Add or adjust tests for every behaviour change, including 400/403/404 paths. When changing routes, config,
  the schema or the rule format, update `docs/DEVELOPMENT.md` and the `README.md`.
