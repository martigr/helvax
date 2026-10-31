"""SQLite access: one connection per request, schema setup and settings."""
import os
import sqlite3
import time

from flask import current_app, g
from werkzeug.security import generate_password_hash

# Shortest allowed password: for users.check_password() and the first admin in init_db()
MIN_PASSWORD_LENGTH = 10

SCHEMA = [
    # Accounts belong to a family; all accounts of a family see and edit all of its profiles
    """
    CREATE TABLE IF NOT EXISTS families (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL COLLATE NOCASE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        display_name TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        email TEXT,
        email_verified INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0,
        family_id INTEGER REFERENCES families(id) ON DELETE SET NULL,
        -- Reminders of due vaccinations (notifications.queue_due_vaccinations()): channels, lead times in
        -- days before the recommended date ('56,7'; '' = none), repeat interval while overdue (0 = off)
        remind_mail INTEGER NOT NULL DEFAULT 1,
        remind_push INTEGER NOT NULL DEFAULT 1,
        remind_leads TEXT NOT NULL DEFAULT '56,7',
        remind_overdue_days INTEGER NOT NULL DEFAULT 30
    )
    """,
    # A person whose vaccinations are recorded (no login of its own)
    """
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        display_name TEXT NOT NULL,
        birth_date TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    # Opt-ins ('optin:<antigen>') and conditions ('fsme_exposure', ...) declared by the schedule
    """
    CREATE TABLE IF NOT EXISTS profile_flags (
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        flag TEXT NOT NULL,
        PRIMARY KEY (profile_id, flag)
    )
    """,
    # One entry of the vaccination record; the antigens it covers are in vaccination_antigens
    """
    CREATE TABLE IF NOT EXISTS vaccinations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        product_key TEXT,
        product_name TEXT NOT NULL,
        lot TEXT NOT NULL DEFAULT '',
        administered_by TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    'CREATE INDEX IF NOT EXISTS vaccinations_profile ON vaccinations (profile_id, date)',
    # dose_override: the dose number as entered, NULL = computed by the engine
    """
    CREATE TABLE IF NOT EXISTS vaccination_antigens (
        vaccination_id INTEGER NOT NULL REFERENCES vaccinations(id) ON DELETE CASCADE,
        antigen TEXT NOT NULL,
        dose_override INTEGER,
        PRIMARY KEY (vaccination_id, antigen)
    )
    """,
    # Immunity without (counted) vaccination: survived disease or serology
    """
    CREATE TABLE IF NOT EXISTS immunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        antigen TEXT NOT NULL,
        kind TEXT NOT NULL,
        date TEXT,
        note TEXT NOT NULL DEFAULT '',
        UNIQUE (profile_id, antigen)
    )
    """,
    # The profiles an account gets reminders for. New profiles are watched by every account of the family,
    # and an account that joins a family watches all of its profiles (profiles.watch_profile(),
    # watch_family()); the reminder scan also checks that the profile is in the account's family.
    """
    CREATE TABLE IF NOT EXISTS profile_watches (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, profile_id)
    )
    """,
    # Reminders already queued, so each one goes out once per account and channel ('mail' / 'push').
    # key: '<profile>:<antigens>:<recommended date>:<lead days>' or '...:overdue:<n>' (see notifications.py).
    # Kept longer than the outbox rows (KEEP_REMINDER_LOG), since a dose can stay due for years.
    """
    CREATE TABLE IF NOT EXISTS reminder_log (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        channel TEXT NOT NULL,
        key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, channel, key)
    )
    """,
    # Browsers that get push messages (push.py), one row per device; created by the button on /account
    """
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        endpoint TEXT UNIQUE NOT NULL,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # Outbox: mails and push messages are queued here and sent by `flask send-notifications`.
    # push_subscription_id is set on push messages (NULL = mail); they go when the device unsubscribes.
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key TEXT UNIQUE NOT NULL,
        to_addr TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        sent_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        push_subscription_id INTEGER REFERENCES push_subscriptions(id) ON DELETE CASCADE
    )
    """,
    # Failed logins of the last minutes, for auth.login_blocked(); key is 'user:<name>' or 'ip:<addr>'
    """
    CREATE TABLE IF NOT EXISTS login_failures (
        key TEXT NOT NULL,
        at TEXT NOT NULL
    )
    """,
    'CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures (key, at)',
    # Audit log (audit.record()); user_id has no foreign key, so entries outlive deleted users
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        user_id INTEGER,
        username TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        ip TEXT
    )
    """,
    'CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log (at)',
]

# Columns added after the first release: (table, column, definition). Applied by init_db()
# to databases created before they existed. Empty until the first release.
ADDED_COLUMNS = []

# audit_days: how long audit log entries are kept
DEFAULT_SETTINGS = {'audit_days': '90'}


def connect():
    con = sqlite3.connect(current_app.config['DATABASE'], timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def get_db():
    """The request's connection. Use `with get_db() as con:` for writes (commits on success)."""
    if 'db' not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    con = g.pop('db', None)
    if con is not None:
        con.close()


def ensure_writable(path):
    """Fail early with a clear message if SQLite can't write the database.

    Typical cause: a Docker volume or host folder owned by a different UID than the app.
    SQLite needs write access to the file and to its directory (journal/WAL files).
    """
    directory = os.path.dirname(os.path.abspath(path))
    # No UIDs on Windows; there os.access only checks the read-only attribute
    uid = os.getuid() if hasattr(os, 'getuid') else None
    as_uid = f' as uid {uid}' if uid is not None else ''
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f'Cannot create the database directory {directory}{as_uid}: {e.strerror}') from None
    for p in [directory, path, path + '-wal', path + '-shm']:
        if os.path.exists(p) and not os.access(p, os.W_OK):
            if uid is None:
                raise RuntimeError(f'{p} is not writable. SQLite needs write access to the database file '
                                   f'and its directory.')
            raise RuntimeError(
                f'{p} is not writable for uid {uid} (owned by uid {os.stat(p).st_uid}). '
                f'SQLite needs write access to the database file and its directory. '
                f'Fix the owner, e.g. chown -R {uid}:{os.getgid()} <folder>; in Docker see "Volumes and permissions" '
                f'in the README.')


def enable_wal(con, timeout=30):
    """Switch the database file to WAL mode (stored in the file, better for parallel workers).

    SQLite doesn't wait for a busy database when changing the journal mode, it fails at once with
    "database is locked". On a new database, a second Gunicorn worker gets there while the first one
    is still in init_db()'s transaction, so retry until that one is done.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            if con.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                con.execute('PRAGMA journal_mode = WAL')
            return
        except sqlite3.OperationalError as e:
            if 'locked' not in str(e) or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def init_db():
    """Create the schema and, on an empty database, the first admin from the config."""
    con = connect()
    con.isolation_level = None  # explicit transaction below
    try:
        enable_wal(con)
        # IMMEDIATE takes the write lock, so parallel Gunicorn workers run this one after another
        con.execute('BEGIN IMMEDIATE')
        for statement in SCHEMA:
            con.execute(statement)
        for table, column, definition in ADDED_COLUMNS:
            if column not in [r['name'] for r in con.execute(f'PRAGMA table_info({table})')]:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

        cfg = current_app.config
        user_count = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if user_count == 0 and cfg['ADMIN_USERNAME'] and cfg['ADMIN_PASSWORD']:
            if len(cfg['ADMIN_PASSWORD']) < MIN_PASSWORD_LENGTH:
                raise RuntimeError(f'ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters long.')
            con.execute(
                'INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, 1)',
                (cfg['ADMIN_USERNAME'], generate_password_hash(cfg['ADMIN_PASSWORD']), cfg['ADMIN_USERNAME']))
            current_app.logger.info('Created admin user %s from ADMIN_USERNAME', cfg['ADMIN_USERNAME'])
        elif user_count and not con.execute('SELECT 1 FROM users WHERE is_admin = 1').fetchone():
            current_app.logger.warning('No admin user yet — run: flask --app helvax make-admin <username>')
        con.execute('COMMIT')
    except Exception:
        if con.in_transaction:
            con.execute('ROLLBACK')
        raise
    finally:
        con.close()


def get_setting(key):
    row = get_db().execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else DEFAULT_SETTINGS[key]


def set_setting(key, value):
    with get_db() as con:
        con.execute('INSERT INTO settings (key, value) VALUES (?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, str(value)))


def init_app(app):
    app.teardown_appcontext(close_db)
