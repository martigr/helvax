# helvax: implementation plan and status

The plan agreed at the start (see `docs/KICKOFF.md` for the original brief), with what is done and what
comes next. Keep it current when a milestone is finished; it is the handover between sessions.

## Decisions

Made with the owner at the kickoff:

- **Real rules from the PDF**, not a placeholder: `helvax/schedules/ch-2026.yaml` encodes the
  *Schweizerischer Impfplan 2026* (`docs/sources/`), every rule with a page reference. Interpretations are
  marked `review: true` and explained in `OPEN_QUESTIONS.md`.
- **Catch-up:** Tab. 2 of the schedule (unvaccinated, by age at the start) plus generic continuation of started
  series with minimum intervals and age limits. Tab. 3/4 (incompletely vaccinated) are not computed: the engine
  shows "Nachholschema mit Ärztin/Arzt festlegen" (`needs_doctor`).
- **Computed from phase 1:** RSV antibodies for infants (by birth month), influenza and Covid-19 yearly from 65,
  FSME (risk group, as a profile flag). Other risk groups and travel vaccinations: category modelled only.
- **Immunity without vaccination** per profile and antigen (disease or serology); "born before 1964" for MMR
  is derived from the birth date.

Defaults chosen without asking:

- No sex field (no rule needs it; HPV is for everyone since 2024).
- The own account is `account.py` (`/account`), so "profile" always means a person whose vaccinations are recorded.
- Admins manage accounts and families but see no health data of families they don't belong to.
- Other families' profiles answer **404**, not 403.
- Extra dependency: PyYAML.
- No iCal feed (decided after phase 1; `docs/KICKOFF.md` still lists it as the original brief).
- Docker host port `127.0.0.1:5050` (5000 may be taken by laundry-booking on the same host).
- Work happens on a branch per phase (`claude/phase-1` is merged); PRs only when the owner asks.

## Milestones

| | Milestone | Status |
|---|---|---|
| M0 | Scaffold from laundry-booking: app factory, db, auth, security, audit, admin with families, account, mail outbox (reset, address confirmation), PWA, CLI, Docker, CI/audit/Dependabot/release workflows | done |
| M1 | `dates.py`, schedule format and loader (`schedule.py`), pure engine (`engine.py`), table-driven tests on `tests/data/test-schedule.yaml` | done |
| M2 | `ch-2026.yaml` with the real rules, `OPEN_QUESTIONS.md`, tests on the real file (`tests/test_schedule_ch.py`) | done |
| M3 | Product catalogue, profiles, vaccination entries, immunities (CRUD, audit, family access) | done |
| M4 | Profile page (status + record table), dashboard, print view, `/schedule`, disclaimer, `seed-demo`, icons, screenshots | done |
| M5 | Docker smoke test and compose checked, README, CLAUDE.md, DEVELOPMENT.md, encrypted backups (`age`) | done |
| M4b | Record as a booklet-style table (`?ansicht=tabelle`), on screen and in print (owner request) | done |
| — | Phase 1 merged into `main` (PR #1, CI green) | done |
| — | **Owner review of phase 1** (rules in `OPEN_QUESTIONS.md`, catalogue, UI) | **open** |
| M6 | Notifications by mail (see below) | open |
| M7 | Web Push (VAPID) | open |
| ~~M8~~ | ~~iCal feed per account~~: dropped by the owner (not much use here) | dropped |
| — | Phase 3 (photos of the booklet, import): designed in `docs/DEVELOPMENT.md`, not built | later |

Phase 1 acceptance criteria (`docs/KICKOFF.md`): all met; CI (ruff, pytest, pip-audit, shellcheck, Docker
build and smoke test) green on PR #1.

## Open points from the phase 1 review

- Medical review of `OPEN_QUESTIONS.md`, in particular the app convention for "overdue" (1 month after the
  recommended date, boosters 1 year) and `skip_from: 16y` for childhood boosters.
- Check the product catalogue `helvax/catalogue/products-ch.yaml` (not from the BAG document).
- ~~`LICENSE` carries the copyright holder's name~~: decided, the name stays.

## Phase 2 design (M6–M7)

Port the matching parts of [laundry-booking](https://github.com/martigr/laundry-booking) (public; the push code was
left out of the phase 1 scaffold) (`laundry/notifications.py`, `push.py`, `profile.py`,
`static/push.js`, `templates/sw.js`, tests `test_notifications.py`, `test_push.py`) and adapt:

**M6 Mail reminders**
- Settings per account on `/account`: channels (`remind_mail`, `remind_push`), lead times
  (`remind_leads`, e.g. `56,7` days = 8 and 1 week before the recommended date), reminder when overdue
  (`remind_overdue_days`, repeat interval, 0 = off), which profiles to watch (`profile_watches (user_id,
  profile_id)`, default: all of the family). Columns go into `db.SCHEMA` directly until the first release.
- `notifications.queue_due_vaccinations(con)` in `run_once()`: evaluate every watched profile with
  `profiles.evaluate()` (needs an app context, no request), group with `profiles.group_actionable()`, and queue
  one message per group and lead time. `dedupe_key`: `due:<profile>:<antigens joined>:<recommended date>:
  <lead>:<user>:<channel>`; overdue repeats `...:overdue:<n>`.
- Text without health details: subject "helvax: Impfung bei Mia", body "Bei Mia ist eine Impfung fällig" +
  link to `/profiles/<id>`; never the antigen or the vaccine. Only confirmed addresses; one-click unsubscribe
  (`/unsubscribe/<token>`, signed, POST to switch off) in every mail.
- Tests with the `mailbox` fixture: text has no antigen names, dedupe, lead times, overdue repeat, other
  families' profiles can't be watched (404/400).

**M7 Web Push**
- `push.py` (pywebpush + VAPID, `flask gen-vapid-key`), `/push/subscribe` and `/push/unsubscribe`,
  `static/push.js`, push handler in `templates/sw.js` (already there), `push_subscriptions` table, rows in the
  outbox per device. Same text rules as mail. Add `pywebpush` to `requirements.txt` and `VAPID_*` to config,
  `.env.example`, README.

After each milestone: `ruff check . && pytest`, update `docs/DEVELOPMENT.md`, README, the privacy page
(`templates/privacy.html`: new data such as push endpoints) and this file.

## Notes for the next session

- Read `CLAUDE.md`, `docs/DEVELOPMENT.md` and this file first.
- Building the Docker image inside a Claude Code cloud sandbox fails at `pip install` because of the sandbox's
  TLS proxy. That is not a repository problem; build with a temporary copy of the Dockerfile that mounts the
  sandbox CA for pip and uses `--network host`, or rely on CI.
- `flask --app helvax --debug seed-demo` gives a fictional family to click through (anna / password123).
