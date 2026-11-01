# Developer Guide

How helvax is built, and how to change it safely. For installation and operation see the
[README](../README.md).

## Overview

A small Flask app for the vaccination records of a family. Accounts log in; *profiles* are the people whose
vaccinations are recorded. All accounts of a family see and edit all of its profiles. The app evaluates each
profile against the Swiss vaccination schedule, which is stored as data (YAML), and shows per antigen what is
due.

- **Backend:** Python package `helvax/` with an app factory and blueprints; SQLite through the standard
  library, no ORM; PyYAML for the rule files.
- **Frontend:** server-rendered Jinja templates, one stylesheet, no build step. JavaScript only where it
  helps and is optional: confirmation dialog (`confirm.js`), password repeat check (`account.js`), ticking the
  antigens of a chosen vaccine (`vaccination.js`), the print button (`print.js`), the push button on `/account`
  (`push.js`), the service worker.
- **UI language:** German (Swiss). Code, comments, commit messages and docs: English.
- Built after the conventions of [laundry-booking](https://github.com/martigr/laundry-booking).

## Project layout

```
helvax/
├── __init__.py           # create_app(): config, rules, blueprints, /health, Jinja filters; __version__
├── dates.py              # now()/today() (the only clock), Duration ('6w', '12m'), add_months, age labels
├── schedule.py           # load()/parse() of a schedule YAML: strict validation, frozen dataclasses
├── engine.py             # evaluate(): status per antigen, next dose, catch-up plan (pure functions)
├── catalogue/            # products-ch.yaml (trade name → antigens, tags) and its loader
├── schedules/ch-2026.yaml  # the Swiss schedule 2026 as rules
├── rules.py              # the schedule and catalogue of the running app (loaded at startup)
├── profiles.py           # bp "profiles": profiles, vaccination entries, immunities, print view
├── dashboard.py          # bp "dashboard": family dashboard (/), schedule page (/schedule)
├── account.py            # bp "account": name, mail address, reminders, push devices, password; links from mails
├── admin.py              # bp "admin": accounts, families, settings, audit log page
├── auth.py               # bp "auth": login/logout, throttling, CSRF, login_required/admin_required, reset
├── audit.py              # audit log: record() of logins and changes, rotation
├── db.py                 # connection per request, SCHEMA, init_db(), settings
├── users.py              # create_user(), get_user*(), set_password(), clean_email(), UserError
├── notifications.py      # outbox: queue_due_vaccinations() (reminders), queue_*(), send_pending(), run_once()
├── mail.py               # is_configured(), deliver(): one plain-text mail over SMTP
├── push.py               # Web Push: VAPID key, valid_subscription(), deliver() (pywebpush)
├── security.py           # security headers (CSP etc.), /.well-known/security.txt
├── privacy.py            # public privacy notice /privacy
├── accesslog.py          # Gunicorn logger class that blanks tokens in the access log
├── pwa.py                # bp "pwa": service worker /sw.js, offline page /offline
├── cli.py, demo.py       # flask commands; fictional demo data for seed-demo
├── importer.py           # CSV import of vaccination entries (parse() checks, save() inserts)
├── evacdoc.py            # eVACDOC (CDA-CH-VACD XML) → rows for the CSV import
├── static/               # style.css, *.js, manifest, icons
└── templates/            # base.html, _status.html (badges), dashboard, profile, profile_form,
                          # vaccination_form, profile_print, _record_matrix (booklet table), schedule,
                          # account, unsubscribe, admin, audit, auth pages
tests/                    # one file per area; data/test-schedule.yaml (made-up rules for engine tests)
docs/sources/             # the BAG schedule PDF the rules are based on
OPEN_QUESTIONS.md         # every interpretation in the rules, for medical review
scripts/                  # docker-smoke-test.sh, backup-nas.sh, icons.py
```

## Running locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
flask --app helvax --debug seed-demo
flask --app helvax --debug run
```

`--debug` matters: without it, `create_app()` refuses to start without a `SECRET_KEY`.

`seed-demo` is a development tool: an idempotent fictional family (Lena, Noah, Anna, Erika) with records whose
dates are relative to today, so something is always due. Tests don't use it; the `app` fixture builds three
accounts (`alice` admin and `bob` in family *Muster*, `carol` in family *Beispiel*) and fixes the clock at
2026-10-15 14:20.

## Data model

```
families                         users
────────                         ─────
id    INTEGER PK                 id, username (lowercase, unique), password (werkzeug hash), display_name,
name  TEXT UNIQUE NOCASE         is_admin, email, email_verified, session_version,
                                 family_id → families ON DELETE SET NULL

profiles                         profile_flags
────────                         ─────────────
id            INTEGER PK         profile_id → profiles ON DELETE CASCADE
family_id     → families CASCADE flag  TEXT   'optin:<antigen>' or a condition declared by the schedule
display_name  TEXT               PRIMARY KEY (profile_id, flag)
birth_date    TEXT ISO
created_at    TEXT

vaccinations                     vaccination_antigens
────────────                     ────────────────────
id, profile_id → CASCADE         vaccination_id → vaccinations CASCADE
date          TEXT ISO           antigen        TEXT  key of the schedule's antigens
product_key   TEXT NULL          dose_override  INTEGER NULL  the dose number as written in the booklet;
product_name  TEXT                               shown only, the engine numbers doses itself
lot, administered_by, note       PRIMARY KEY (vaccination_id, antigen)
created_at, updated_at

immunities                       notifications (outbox), login_failures, settings, audit_log
──────────                       as in laundry-booking (see db.py); notifications.push_subscription_id
id, profile_id → CASCADE         → push_subscriptions CASCADE (NULL = mail)
antigen, kind ('disease' | 'serology'), date NULL, note
UNIQUE (profile_id, antigen)

users (reminder columns)         profile_watches                  reminder_log
────────────────────────         ───────────────                  ────────────
remind_mail     INTEGER 1        user_id    → users CASCADE       user_id → users CASCADE
remind_push     INTEGER 1        profile_id → profiles CASCADE    channel 'mail' | 'push'
remind_leads    TEXT '56,7'      PRIMARY KEY (user_id,            key     see Reminders
remind_overdue_days INTEGER 30                profile_id)         created_at
                                                                  PRIMARY KEY (user_id, channel, key)
push_subscriptions
──────────────────
id, user_id → users CASCADE, endpoint UNIQUE, p256dh, auth, created_at
```

- **Per antigen:** a combination vaccine is one `vaccinations` row with one `vaccination_antigens` row per
  antigen. `product_key` points into the catalogue (NULL = "Anderer Impfstoff"); `product_name` is stored too,
  so renaming the catalogue never changes old entries.
- Deleting a profile deletes its entries, flags and immunities. Deleting an account keeps the family's profiles;
  a family with profiles can't be deleted.
- `PRAGMA foreign_keys = ON` on every connection; WAL mode; `init_db()` in a `BEGIN IMMEDIATE` transaction.
- Migrations: new tables go into `SCHEMA`; columns added after the first release into `db.ADDED_COLUMNS` with a
  test that builds the old schema (`test_columns_are_added_to_an_existing_database`).

## Reminders and push (phase 2)

- **Settings** per account on `/account` (`action=reminders`): channels (`remind_mail`, `remind_push`), lead
  times in days before the recommended date (`remind_leads`, choices in `notifications.LEAD_CHOICES`), the repeat
  interval while overdue (`remind_overdue_days`, `OVERDUE_CHOICES`, 0 = off), and the watched profiles
  (`profile_watches`). Profiles are created only through `profiles.create_profile()`, which makes every account of
  the family watch the new profile; an account moved to another family by an admin watches all of that family's
  profiles (`profiles.watch_family()`), and the scan only follows watches inside the account's own family.
- **Scan:** `notifications.run_once()` (the notifier, every minute) calls `queue_due_vaccinations(con)`: for each
  account with a usable channel (mail: on and a confirmed address; push: on, configured and at least one device)
  it evaluates each watched profile with `profiles.evaluate()` (needs an app context, no request) and groups the
  statuses with `profiles.group_actionable()`. A group (one combination vaccine) is identified by
  `<profile>:<antigens>:<date>`, the date being the recommended one, or the earliest one for "as soon as possible"
  doses (their recommended date is always today). Not yet overdue: the smallest lead time already reached gives
  the key `...:<lead>` (so a profile created late doesn't get the 8-week and the 1-week reminder at once).
  Overdue: `...:overdue:<day>` when the last overdue reminder of the group is at least `remind_overdue_days` old.
  Keys already in `reminder_log` for that account and channel are skipped.
- **One message per profile and run:** all new keys of a profile go into one mail (and one push per device). The
  outbox `dedupe_key` is `due:<profile>:<user>:<time>:mail` or `...:push:<subscription>`; the dedupe across runs is
  `reminder_log`, which is kept 10 years (`KEEP_REMINDER_LOG`), because a dose can stay due for years (age windows)
  and the outbox is pruned after 30 days. Reminders expire after 7 days unsent (`REMINDER_VALID`).
- **Texts:** subject "helvax: Impfung bei Mia", "Bei Mia ist bald eine Impfung fällig." / "... ist eine Impfung
  fällig." / "... überfällig.", the link to `/profiles/<id>`, and in mails a footer with the one-click
  unsubscribe link (`/unsubscribe/<token>`, signed, GET shows a button, POST sets `remind_mail = 0`) and
  `/account`. Never the vaccine, antigen or disease (tests check every antigen key, label, short name and product
  name against mails and push payloads).
- **Push** (`push.py`, ported from laundry-booking): `VAPID_PRIVATE_KEY` from `flask gen-vapid-key`; `push.js`
  subscribes the browser and posts endpoint and keys to `/push/subscribe` (form fields with CSRF token; only https
  endpoints of the known push services, keys of the right size; at most 10 devices per account; a device belongs
  to whoever subscribed it last). The payload is JSON `{title, body, url, tag}` shown by `templates/sw.js`. A 404/410
  from the push service deletes the subscription. `to_addr` of push rows is only the push service's host.

### Phase 3 (designed, not built)

- **Photos of the paper booklet:** table `attachments (id, vaccination_id → CASCADE, stored_name, mime, size,
  sha256, created_at)`; files under `/data/attachments/<random name>`, never in the DB and never under a path
  derived from user input; size limit (e.g. 5 MB, `MAX_CONTENT_LENGTH` raised only for that route); only
  JPEG/PNG/PDF by content sniffing; served through a family-checked route with `Content-Disposition:
  attachment` and `no-store`; included in backups (backup command then archives the folder too).
- **Import (CSV) in the web interface, maybe OCR:** reuse `importer.parse()` (built for the command line, see
  below) for an upload form with a preview step before saving.

### CSV import (command line)

`flask import-vaccinations <username> <profile> <file>` (`cli.py`, logic in `importer.py`). The profile is
looked up by id or name only within the user's family (same rule as `get_family_profile()`). `importer.parse()`
turns every row into the fields of the vaccination form (`MultiDict`) and checks it with
`profiles.parse_vaccination_form()`, so an import accepts exactly what the form accepts; it also rejects
unknown columns, products and antigens, and `doses` for antigens the vaccine doesn't cover. It returns
`(entries, errors, skipped)`: rows with the same date and product name as an existing entry (or an earlier
row) are skipped. Any error: nothing is saved. Otherwise the command prints the entries, asks (unless
`--yes`; `--dry-run` stops before), inserts them with `profiles.create_entry()` in one transaction and writes
one audit entry `vaccinations_imported` (`<profile>: <count>`, user `Kommandozeile (<username>)`). The output
on the terminal names vaccines (the user asked for it); nothing about them goes into logs or the audit log.
`flask list-products [search] [--antigens]` prints the catalogue keys and antigen keys for the file.

`flask convert-evacdoc <xml> <csv>` (`evacdoc.convert()`, standard-library ElementTree; files with a DOCTYPE
or ENTITY are refused, at most 10 MB) writes such a CSV (UTF-8 with BOM, `;`) from an eVACDOC document. It
only produces a file to check and edit; the import does the validation. The mapping rules (SNOMED codes →
antigens, when a catalogue product is used) are in `OPEN_QUESTIONS.md`; the test file
`tests/data/evacdoc-test.xml` is made up.

## Schedule file

`helvax/schedules/ch-2026.yaml` (path: `SCHEDULE_FILE`) is loaded and validated at startup by
`schedule.load()`; an invalid file stops the app with a message naming the place. Its version
(`ch-2026 (Stand 16.04.2026)`) and source are shown on every profile, the dashboard and `/schedule`.

```yaml
schedule: {id, title, publisher, published, revised, source_url, overdue_grace: 1m, placeholder: false}
antigens:  {<key>: {label, short}}            # e.g. dip, tet, pert, polio, hib, hbv, pcv, ... tbe
  # record_only: true = no rule (menc, bcg, hav, ppv23, travel vaccines, ...): recorded, never evaluated
flags:     {<key>: {label}}                   # conditions; 'optin:<antigen>' flags are generated
rules:
  - antigen: dip
    category: basic                           # basic | supplementary | risk_group | travel
    ref: "S. 6 Tab. 1 Fn. 13; ..."            # where in the PDF
    review: true                              # interpretation, listed in OPEN_QUESTIONS.md
    note: German hint shown on /schedule
    requires_flag / skip_if_flag: <flag>      # e.g. FSME only with fsme_exposure; RSV not with rsv_maternal
    exempt_if_born_before: 1964-01-01         # MMR
    not_counted_before: 9m                    # MMR doses before 9 months don't count
    booster_grace: 1y                         # boosters are overdue this long after their date
    courses:                                  # independent parts, e.g. infant and adolescent meningococci
      - id: grundimmunisierung
        ages: [0d, null]                      # a dose belongs to the course whose ages contain it
        category: supplementary               # optional, overrides the rule's
        series:                               # chosen by the age at the course's first dose (or today)
          - start_age: [0d, 3m]
            catch_up: false                   # true: "as soon as possible" doses are already overdue
            category: ...                     # optional (HPV 20–26 is supplementary)
            doses:
              - {at: 2m, min_age: 6w}
              - {at: 4m, min_interval: 4w}
              - {at: 12m, min_age: 11m, min_interval: 6m, overdue_after: 13m}
              - {window: [4y, 8y], min_interval: 2y, if_missed: doctor, skip_from: 16y, label: ...}
              - {after: 2m, min_interval: 2m, if_previous_product: [menveo]}  # MenACWY: only after Menveo
              - {products: [pcv20, pcv21], min_interval: 1y}  # PCV from 65: only these fill the slot
              - {optional: true, until: 11m, min_interval: 4w}  # former 3+1: 3rd dose at 6 months
          - start_age: [3m, 6m]
            catch_up: true
            doses: [{}, {after: 2m, min_interval: 4w}, {after: 6m, min_interval: 6m}]
        birth_month:                          # instead of series: one dose timed by birth month (RSV)
          - {birth_months: [10, 11, 12, 1, 2, 3], at: 0d, latest: 7d, missed_after: 03-31, missed_then: 10-01}
          - {birth_months: [4, 5, 6, 7, 8, 9], on_date: 10-01, latest_date: 10-31}
    recurring:                                # boosters after the courses; the age at the last dose decides
      - {age_at_last: [0d, 16y], next_at_age: 25y}
      - {age_at_last: [25y, 65y], interval: 20y}
    seasonal: {from_age: 65y, recommend: [10-15, 12-31], counts_from: 09-01, min_interval: 6m}
```

- **Durations** are calendar durations from the birth date or the previous dose: `14d`, `6w`, `12m`, `25y`
  (`dates.Duration`; months clamp to the end of shorter months). **Age ranges** are `[from, until)`; `null` =
  no limit. "4–7 Jahre" is `[4y, 8y]`.
- **Dose timing:** one of `at` (age), `window` (age range; overdue after it), `after` (interval after the
  previous dose), or none = "as soon as possible". `min_age`/`min_interval` give the earliest date;
  `overdue_after` an explicit deadline; `until` an age from which the dose can't be given any more
  (`if_missed: skip` ends the series, `doctor` shows the doctor hint, `overdue` keeps it overdue);
  `skip_from` drops the slot from that age on (adults don't catch up childhood boosters), and a dose given
  at that age counts as a later dose instead.
- **Product-dependent doses** use the `tags` of the catalogue products (`products-ch.yaml`); the catalogue
  must have every tag the rules name (checked at startup). `products: [tags]`: only a dose of such a product
  fills the slot; a dose of another known product is passed over (it keeps its number, and `min_interval`
  counts from it). `if_previous_product: [tags]`: the slot exists only if the previous dose was such a
  product; before that dose is given, the slot isn't planned. A dose of an unknown product ("Anderer
  Impfstoff", or a product no longer in the catalogue) fills a `products` slot and doesn't open an
  `if_previous_product` slot, both with a warning.
- **Optional doses:** `optional: true` (needs `until`) is a slot that only a dose given before `until` fills;
  when the next dose comes later, the slot is dropped. It is never planned. Used for the third infant dose at
  6 months of the 3+1 schedule before 2019 (S. 9), so such records count 4 doses before the boosters without
  interval warnings.
- **Record-only antigens** (`record_only: true`) have no rule: entries can name them (form, catalogue, CSV and
  eVACDOC import), the record numbers their doses, but `profiles.evaluate()` doesn't pass them to the engine,
  so they get no status, no column status in the matrix, no reminders and no immunity. Every other antigen
  needs a rule, and a record-only antigen can't have one (checked at startup). `Schedule.evaluated_antigens` /
  `record_only_antigens` split them for the form («Ohne Auswertung (nur erfasst)») and `/schedule`.
- YAML anchors (`&dt_courses`, `*dt_courses`) share identical rules (diphtheria/tetanus, MMR).
- Don't name a key `on`, `yes` or `no`: YAML 1.1 reads them as booleans.

## Engine

`engine.evaluate(profile, doses, schedule, today, horizon=90 days)` → one `AntigenStatus` per rule:

- `status`: `overdue` > `due` > `upcoming` > `complete` > `not_recommended` (`STATUS_LABELS` gives the German
  words). `upcoming` = a series in progress, or something recommended within `horizon`. A rule that only
  starts later (HPV at 11) is `not_recommended` with `next` set and the reason "Empfohlen ab ...".
- `next` (`PlannedDose`): `number`, `earliest`, `recommended`, `latest` (overdue after), `asap`, `label`.
- `plan`: the missing doses from today on, each respecting the minimum intervals after the previous one
  (assumed to be given on its recommended day, or today if that has passed) and the age limits.
- Input: `Dose(date, antigen, ref, product)`, one per antigen of an entry; `product` is the frozenset of the
  catalogue product's `tags`, `None` if the product is unknown (`profiles.evaluate()` builds them).
- `doses`: the given doses with their numbers (`None` = not counted); `warnings` for doses given too early,
  too soon after the previous one, or of an unknown product where the rule depends on it;
  `reason` for `not_recommended`; `needs_doctor` when the schedule's detailed catch-up tables would be needed.

How a rule is evaluated: immunity, `exempt_if_born_before`, `requires_flag`, `skip_if_flag` first; then each
course (series chosen by the age at its first dose; given doses fill the series in order; the first missing
dose decides the status); then `recurring` once every course is through; then `seasonal`. The most urgent part
wins. `number_doses()` numbers the doses per antigen for the record table; `summary()` sorts the actionable
statuses; `profiles.group_actionable()` merges statuses with the same next dose (one combination vaccine) for
display.

The engine never reads the clock, the DB or Flask; it is tested table-driven in `tests/test_engine.py` against
`tests/data/test-schedule.yaml` (made-up rules, one feature each) and against the real rules in
`tests/test_schedule_ch.py` (life courses: infant on time, missed doses, late start, adult boosters, combination
vaccines, opt-ins, RSV by birth month, seasonal, FSME, HPV).

## Request flow

- `before_request`: `check_csrf()` for every POST, then `load_user()` (ends sessions of deleted users and after
  a password change via `session_version`).
- `after_request`: `security.add_headers()`.
- `@login_required` redirects to the login page; `@admin_required` answers 403 for non-admins.
- Profiles and entries are loaded only through `profiles.get_family_profile()` / `get_family_vaccination()`:
  `WHERE ... AND family_id = g.user.family_id`, 404 otherwise, so other families' ids can't be probed.
- State changes are form POSTs answered with a redirect; feedback via `flash(message, 'ok' | 'warn')`.
  Invalid form input re-renders the form with status 400.

### Routes

| Route | Method | Access | Purpose |
|---|---|---|---|
| `/` | GET | user | Family dashboard: what is due next, profiles with their most urgent status |
| `/schedule` | GET | user | Schedule version, source, rules with category, notes, page references |
| `/profiles/new` | GET/POST | user with family (403 without) | `display_name` (1–40), `birth_date` (not in the future), `flags` (repeated; 400 for unknown) |
| `/profiles/<id>` | GET | family | Status overview and vaccination record; `?ansicht=liste` (default) or `tabelle` (matrix like the paper booklet: a row per entry, a column per antigen that occurs in the record or is needed now (overdue, due, upcoming), dose numbers in the cells, a status row at the bottom, so a vaccination never given shows as an empty column with its status; `profiles.record_matrix()`); 400 for other values |
| `/profiles/<id>/edit` | GET/POST | family | Same fields; the birth date can't move after the first vaccination |
| `/profiles/<id>/delete` | POST | family | Deletes entries, flags, immunities |
| `/profiles/<id>/print` | GET | family | Print view (print CSS; "Save as PDF" in the browser); same `?ansicht=` |
| `/profiles/<id>/vaccinations/new` | GET/POST | family | `date` (birth ≤ date ≤ today), `product` (catalogue key or `other` with `product_name` and `antigens`), `lot` (40), `administered_by` (80), `note` (200), `another=1` to add the next |
| `/vaccinations/<id>/edit` | GET/POST | family | Same, plus `dose_<antigen>` (1–20, empty = computed) |
| `/vaccinations/<id>/delete` | POST | family | |
| `/profiles/<id>/immunities` | POST | family | `antigen` (one with a rule; 400 for record-only ones), `kind` (`disease`/`serology`), optional `date`, `note`; one per antigen |
| `/immunities/<id>/delete` | POST | family | |
| `/account` | GET/POST | user | `action=name`, `action=email` (needs confirming), `action=verify_email`, `action=password`, `action=reminders` (`remind_mail`, `remind_push`, `leads` repeated, `overdue_days`, `watch` repeated profile ids; 400 for values not offered or profiles of other families) |
| `/push/subscribe` | POST | user | `endpoint`, `p256dh`, `auth` from `push.js`; 204; 400 for invalid ones; 404 without push config |
| `/push/unsubscribe` | POST | user | `endpoint`; 204; 400 when empty |
| `/unsubscribe/<token>` | GET/POST | token | Signed link in reminder mails; GET shows a button, POST switches mail reminders off; 404 for invalid tokens |
| `/verify-email/<token>` | GET/POST | token | Signed, 7 days, only for the address it was sent to; GET shows a button |
| `/admin` | GET | admin | Accounts, families, settings |
| `/admin/users` | POST | admin | Create account |
| `/admin/users/<id>/reset`, `/delete`, `/family` | POST | admin | New password (shown once), delete (not yourself), assign family (`family_id`, empty = none) |
| `/admin/families`, `/admin/families/<id>/delete` | POST | admin | Create (1–40 characters, unique); delete only without profiles |
| `/admin/settings` | POST | admin | `audit_days` (1–3650) |
| `/admin/audit` | GET | admin | `?kind=login\|record\|settings\|admin`, `?page=N` |
| `/login`, `/logout`, `/forgot-password`, `/reset-password/<token>` | | public | As in laundry-booking |
| `/health`, `/privacy`, `/sw.js`, `/offline`, `/.well-known/security.txt` | GET | public | |

## Security

- **Health data:** login required everywhere except login, password reset, privacy, health, offline page,
  service worker and `security.txt`. Pages are `Cache-Control: no-store`, the service worker never caches
  pages, `X-Robots-Tag: noindex`.
- **Family isolation** as described under *Request flow*; tests in `test_profiles.py`
  (`test_other_family_gets_404`). Admins manage accounts and families but see no profiles of other families.
- **No health details** outside the pages: the audit log names the profile only ("Impfung erfasst · Mia");
  reminders by mail and push say "Bei Mia ist eine Impfung fällig" plus a link, never the vaccine; URLs carry
  ids, not data; the access log blanks tokens.
- **Push endpoints** are user input: only https URLs of the browsers' push services are accepted, so the
  notifier can't be made to POST to internal addresses.
- **CSRF** on every POST (hand-written check, `secrets.compare_digest`); **sessions** end on password change;
  **login throttling** (10 per user, 20 per address in 15 minutes); **CSP** without inline scripts; HSTS over
  HTTPS; `MAX_CONTENT_LENGTH` 64 KB; passwords 10–128 characters, scrypt hashes.
- **Escaping:** Jinja autoescape; confirmation texts in `data-confirm*` attributes, inserted with `textContent`.
- **Container:** starts as root only to `chown /data`, then `setpriv` to `PUID:PGID`; port published on
  `127.0.0.1` only; `no-new-privileges`.
- **Backups** contain health data: encrypt them (`AGE_RECIPIENT` in `scripts/backup-nas.sh`, see README).

## Frontend

- Design tokens in `:root` of `style.css`. Status colours: red = overdue, orange = due, grey = upcoming,
  green = complete, grey = not recommended. Print styles (`@media print`) hide navigation and buttons.
- The footer of logged-in pages shows the version and the disclaimer; logged-out pages show neither.
- Home screen icons are committed PNGs drawn by `python scripts/icons.py` (needs Pillow) from `_logo.html`.
- README screenshots in `docs/screenshots/` are taken from `seed-demo` data only.

## Testing

| Fixture | Gives you |
|---|---|
| `app` | Temp DB; `alice` (admin) + `bob` in family 1 *Muster*, `carol` in family 2 *Beispiel*; clock 2026-10-15 14:20 |
| `client`, `login`, `csrf`, `query` | Test client; `login('bob')` returns a CSRF token; `query(sql, *params)` → tuples |
| `mailbox` | Mails "sent" as `(to, subject, body)`; mail is configured with `BASE_URL=https://hx.test` |
| `pushbox` | Switches push on (`VAPID_KEY`); pushes "sent" as `(endpoint, payload dict, ttl)` |

CI (`.github/workflows/ci.yml`): `ruff check .`, `pytest`, `shellcheck`, Docker build and
`scripts/docker-smoke-test.sh`. `audit.yml` runs `pip-audit` on every PR, push to `main` and weekly.
Dependabot proposes grouped updates weekly. Actions are pinned to commit SHAs. To keep Actions minutes low
there is no matrix build; multi-arch images are built only by the release workflow.

**Release** (`.github/workflows/release.yml`): bump `version` in `pyproject.toml` and `__version__` in
`helvax/__init__.py`, merge, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The workflow runs CI, builds
`linux/amd64` + `linux/arm64`, pushes `ghcr.io/martigr/helvax:X.Y.Z`, `X.Y`, `X`, `latest` (pre-releases only
their exact tag) and creates the GitHub release. The package is private while the repository is private (see
README for pulling it with a `read:packages` token).

## Updating the schedule

When the BAG publishes a new schedule (usually in the first quarter):

1. Put the PDF into `docs/sources/` and list it in `docs/sources/README.md`.
2. Copy `ch-2026.yaml` to `ch-<year>.yaml`, update the header, go through the changes rule by rule with
   page references, and update `OPEN_QUESTIONS.md`.
3. Point `SCHEDULE_FILE`'s default in `__init__.py` to the new file; adjust `tests/test_schedule_ch.py`.
4. The catalogue (`products-ch.yaml`) needs updates when vaccines come and go; keep old products, old records
   name them (unauthorised ones go into the group «Nicht in der Schweiz zugelassen»). Check it against the
   Swissmedic list "Zugelassene Humanarzneimittel" (Excel, column "Heilmittelcode" = `Impfstoffe`, plus the RSV
   antibodies) at https://www.swissmedic.ch/swissmedic/de/home/services/listen_neu.html, and the antigens against
   the ingredients in the SL: `https://epl.bag.admin.ch/api/sl/public/medicinal-products?page=1&size=100&search=<name>`
   (public JSON, IT code `08.08.` = vaccines; the SL only lists reimbursed products). Record the date of the
   check in the file header and in `OPEN_QUESTIONS.md`.
