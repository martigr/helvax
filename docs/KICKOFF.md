# helvax – digital vaccination record (Claude Code kickoff prompt)

## Goal

Build **helvax**, a small self-hosted web app that keeps the vaccination records of a family
(several person profiles), knows the Swiss vaccination schedule (BAG / EKIF "Schweizerischer
Impfplan") and reminds the right people in time when a vaccination becomes due.

Runs in my homelab (Docker on Proxmox), reachable only internally via Tailscale, behind
Nginx Proxy Manager with TLS. It still must be secure on its own: it stores health data.

## Reference project: follow it closely

Use my repo **https://github.com/martigr/laundry-booking** as the template for stack, structure and
conventions. Clone it and read `CLAUDE.md`, `docs/DEVELOPMENT.md`, `README.md`, `SECURITY.md`
before writing any code. Adopt, unless something below says otherwise:

- Flask app factory + blueprints, SQLite via stdlib `sqlite3` (no ORM), schema in `db.SCHEMA` /
  `init_db()` with `ADDED_COLUMNS` migrations, `get_db()` per request
- Server-rendered Jinja, one `style.css` with CSS variables, no JS framework, no build step,
  no CDNs/external fonts, no inline scripts (strict CSP)
- Single clock `now()` that tests can replace
- Auth: login, login throttling, CSRF on every POST form, sessions end on password change,
  security headers, `noindex`, audit log (`audit.record()`), privacy page
- Notifications through an outbox (`queue_*()` with `dedupe_key`, `flask send-notifications --loop`),
  mail via `smtplib`, Web Push via `pywebpush` + VAPID, verified addresses only, one-click unsubscribe
- Personal iCal feed with a secret, renewable link
- PWA (manifest, icons, service worker with offline page, pages never cached)
- Dockerfile + docker-compose (web + notifier, port bound to 127.0.0.1), entrypoint with PUID/PGID,
  health endpoint, backup CLI
- `pyproject.toml`, pinned `requirements*.txt`, ruff + pytest, GitHub Actions (CI, audit, Dependabot)
- **UI German (Swiss style, "ss" not "ß"); code, comments, docs, commits English**
- Write a `CLAUDE.md` and `docs/DEVELOPMENT.md` for this repo in the same style

Package name: `helvax`. Python 3.13.

## Domain

**Accounts vs. profiles.** An *account* logs in. A *profile* is a person whose vaccinations are
recorded (adult or child, no login of its own). Accounts belong to a *family*; all accounts of a family
can see and edit all profiles of that family. Admin creates accounts and families.
Profile fields: display name, date of birth, optional sex (only if a rule needs it),
optional flags for supplementary recommendations (see below).

**Vaccination entries.** Date, product (trade name), the antigens it covers, dose (computed, editable),
lot number, administered by (doctor/pharmacy, free text), note. Manual entry in phase 1.
Combination vaccines are the normal case (e.g. DTPa-IPV-Hib-HBV), so **all logic works per antigen**,
not per product. Ship a product catalogue (YAML) that maps Swiss trade names to antigens;
allow a free "other product" with manually chosen antigens.

**Schedule rules.** The Swiss schedule as a **versioned, data-driven YAML file** (e.g.
`helvax/schedules/ch-2026.yaml`) with source reference and version shown in the UI. Per antigen:
recommended doses with age windows, minimum intervals between doses, boosters (incl. adult
boosters like dT every 20/10 years), catch-up rules by age, and the category:
- `basic` (Basisimpfungen)
- `supplementary` (ergänzende Impfungen), opt-in per profile
- `risk_group` and travel vaccinations: model the category, implement later

**Important:** do not invent medical rules. I will provide the current BAG schedule PDF. Until then,
build the rule format and engine with a clearly marked placeholder dataset, and list every rule
you were unsure about in an `OPEN_QUESTIONS.md`. Show a disclaimer in the UI: no medical advice,
check with your doctor.

**Engine.** Pure functions (no DB, no Flask) that take a profile, its entries, the rules and a date
and return per antigen: status (`complete`, `upcoming`, `due`, `overdue`, `not_recommended`),
next due date / window, and the catch-up plan if doses were missed (respecting minimum intervals
and age limits). Must be deterministic and heavily unit-tested with table-driven cases
(infant series, missed doses, late start, adult boosters, combination products).

## Features

Phase 1 (MVP):
- Accounts, families, profiles, manual vaccination entries (create/edit/delete, audit-logged)
- Per profile: vaccination record table (like the paper booklet) and a status overview
  ("fällig", "überfällig", "demnächst", "vollständig")
- Family dashboard: what is due next across all profiles
- Printable view per profile (print CSS, browser "Save as PDF"); no PDF library

Phase 2 (notifications):
- Per account, configurable: channels (mail, Web Push), lead times (e.g. 8 weeks and 1 week before),
  reminder when overdue (repeat interval), which profiles to watch
- iCal feed with due dates per account (all watched profiles)

Phase 3 (later, only design for it now):
- Photo/scan of the paper booklet attached to entries (stored outside the DB, size-limited)
- Import (e.g. CSV), maybe OCR

## Security and privacy

Treat all of it as health data: login required everywhere except login/privacy/health,
no data in URLs or logs (tokens redacted), no health details in mail or push bodies
("Bei Max ist eine Impfung fällig" + link, not the antigen), audit log for every change,
backups via CLI (document encrypting them on the NAS). No telemetry, no external requests.

## Repository: private now, public later

The GitHub repo `martigr/helvax` starts **private** and may become public later. Build it so that
flipping it to public needs no cleanup:

- No secrets, real names, addresses, hostnames, IPs or vaccination data anywhere in the repo or its history:
  config only via `.env` (`.env.example` with placeholders), demo/test data clearly fictional,
  screenshots only from `seed-demo` data
- `SECURITY.md`, `LICENSE` (MIT, as in the reference) and README written for a public audience from the start
- Leave out the GitHub Pages workflow (not available for private repos on the free plan); keep CI, audit,
  Dependabot and the release workflow
- Release images go to `ghcr.io/martigr/helvax` and are private while the repo is private: document in the
  README how the homelab pulls them (`docker login ghcr.io` with a fine-grained PAT, `read:packages` only)
- Keep Actions minutes low: no matrix builds, multi-arch (amd64 + arm64) only in the release workflow

## How to work

1. Start in plan mode: read the reference repo, then propose data model, rule-file format, engine API,
   routes and a milestone plan. Ask me where something is unclear. Wait for my OK.
2. Implement phase 1 in small, tested steps; `ruff check . && pytest` green before every commit.
3. Then phase 2.

## Acceptance criteria (phase 1)

- `docker compose up -d --build` starts the app; `/health` ok; smoke test script passes
- Two accounts in one family see the same profiles; an account of another family gets 403/404
- Entering a combination vaccine updates the status of every antigen it covers
- Engine tests cover on-time series, missed doses with catch-up, adult boosters, supplementary opt-in
- Rule file version and source are visible; changing the YAML changes the results without code changes
- CI (ruff, pytest, pip-audit, shellcheck, docker build) green; README, CLAUDE.md, DEVELOPMENT.md written
