import hashlib
import re
import secrets
import sqlite3
import time

from .store import Conflict, token_hash

LOGIN_SECONDS = 8 * 60 * 60
THEMES = ("system", "light", "dark")
PREFERENCE_OPTIONS = {
    "theme": THEMES,
    "start_page": ("overview", "conditions", "sessions", "session-chat"),
    "sidebar": ("open", "closed"),
    "chat_scope": ("study", "preview", "all"),
}


def _public(row):
    return {key: row[key] for key in ("id", "username", "role", "disabled", "display_name", *PREFERENCE_OPTIONS)}


def _password_bytes(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("Enter a valid password.")
    return value.encode()


def _derive(password, salt):
    return hashlib.scrypt(password, salt=salt, n=2**15, r=8, p=3, maxmem=64*1024*1024)


def _credentials(username, password):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,39}", username.strip()):
        raise ValueError("Use 3–40 letters, numbers, dots, underscores, or hyphens for the username.")
    raw = _password_bytes(password)
    if len(password) < 12:
        raise ValueError("Use a password with at least 12 characters.")
    salt = secrets.token_bytes(16)
    return username.strip(), salt.hex() + ":" + _derive(raw, salt).hex()


def _matches(password, stored):
    raw = _password_bytes(password)
    salt, expected = stored.split(":") if stored else ("00"*16, "00"*64)
    candidate = _derive(raw, bytes.fromhex(salt)).hex()
    return bool(stored) and secrets.compare_digest(candidate, expected)


def _new_login(db, account):
    now = time.time()
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM researcher_logins WHERE expires_at <= ?", (now,))
    db.execute("INSERT INTO researcher_logins VALUES(?,?,?)", (token_hash(token), account["id"], now + LOGIN_SECONDS))
    return token


def configured(store):
    with store.db() as db:
        return db.execute("SELECT username IS NOT NULL FROM researcher_accounts WHERE id=1").fetchone()[0] == 1


def legacy_login(store):
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        owner = db.execute("SELECT * FROM researcher_accounts WHERE id=1 AND username IS NULL").fetchone()
        if owner is None:
            raise PermissionError("Sign in with your researcher account.")
        return _new_login(db, owner)


def sign_in(store, username, password):
    if not isinstance(username, str) or not 1 <= len(username) <= 40:
        raise PermissionError("Invalid username or password.")
    with store.db() as db:
        row = db.execute("SELECT * FROM researcher_accounts WHERE username=?", (username.strip(),)).fetchone()
    stored = row["password_hash"] if row else None
    if not _matches(password, stored) or row["disabled"]:
        raise PermissionError("Invalid username or password.")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        account = db.execute("SELECT * FROM researcher_accounts WHERE id=? AND password_hash=? AND disabled=0",
                             (row["id"], stored)).fetchone()
        if account is None:
            raise PermissionError("Sign in with your current password.")
        return _new_login(db, account)


def get_account(store, token):
    if not token:
        return None
    with store.db() as db:
        row = db.execute("""SELECT a.* FROM researcher_logins l JOIN researcher_accounts a ON a.id=l.researcher_id
                          WHERE l.token_hash=? AND l.expires_at>? AND a.disabled=0""",
                         (token_hash(token), time.time())).fetchone()
    return _public(row) if row else None


def sign_out(store, token):
    if token:
        with store.db() as db:
            db.execute("DELETE FROM researcher_logins WHERE token_hash=?", (token_hash(token),))


def claim_owner(store, username, password):
    name, password_hash = _credentials(username, password)
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        updated = db.execute("UPDATE researcher_accounts SET username=?,password_hash=? WHERE id=1 AND username IS NULL",
                             (name, password_hash))
        if updated.rowcount != 1:
            raise Conflict("The owner account has already been created.")
        db.execute("DELETE FROM researcher_logins WHERE researcher_id=1")
        return _new_login(db, {"id": 1})


def change_password(store, account, current, password):
    with store.db() as db:
        row = db.execute("SELECT password_hash FROM researcher_accounts WHERE id=?", (account["id"],)).fetchone()
    stored = row["password_hash"]
    if not _matches(current, stored):
        raise PermissionError("The current password is incorrect.")
    _, replacement = _credentials(account["username"], password)
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        updated = db.execute("""UPDATE researcher_accounts SET password_hash=?
                             WHERE id=? AND password_hash=? AND disabled=0""", (replacement, account["id"], stored))
        if updated.rowcount != 1:
            raise Conflict("The account changed. Sign in again before updating your password.")
        db.execute("DELETE FROM researcher_logins WHERE researcher_id=?", (account["id"],))
        return _new_login(db, account)


def set_theme(store, account, theme):
    if theme not in THEMES:
        raise ValueError("Choose System, Light, or Dark.")
    return update_preferences(store, account, {"theme": theme})["theme"]


def update_preferences(store, account, changes):
    if not changes or not changes.keys() <= {*PREFERENCE_OPTIONS, "display_name"}:
        raise ValueError("Choose a valid account preference.")
    values = dict(changes)
    for key, value in values.items():
        if key == "display_name":
            if not isinstance(value, str) or len(value) > 80:
                raise ValueError("Use a display name of up to 80 characters.")
            values[key] = " ".join(value.split())
        elif value not in PREFERENCE_OPTIONS[key]:
            raise ValueError("Choose a valid account preference.")
    assignments = ", ".join(f"{name}=?" for name in values)
    with store.db() as db:
        changed = db.execute(f"UPDATE researcher_accounts SET {assignments} WHERE id=? AND disabled=0",
                             (*values.values(), account["id"]))
        if not changed.rowcount:
            raise PermissionError("Researcher sign-in is required.")
        return _public(db.execute("SELECT * FROM researcher_accounts WHERE id=?", (account["id"],)).fetchone())


def activity_counts(store, account_id):
    with store.db() as db:
        login_count = db.execute("SELECT COUNT(*) FROM researcher_logins WHERE researcher_id=? AND expires_at>?",
                                 (account_id, time.time())).fetchone()[0]
        chat_count = db.execute("SELECT COUNT(*) FROM researcher_chats WHERE researcher_id=?", (account_id,)).fetchone()[0]
    return {"login_count": login_count, "chat_count": chat_count}


def revoke_other_logins(store, account, token):
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        current = token_hash(token)
        timestamp = time.time()
        if not db.execute("""SELECT 1 FROM researcher_logins WHERE researcher_id=?
                             AND token_hash=? AND expires_at>?""", (account["id"], current, timestamp)).fetchone():
            raise PermissionError("Researcher sign-in is required.")
        return db.execute("""DELETE FROM researcher_logins WHERE researcher_id=?
                             AND token_hash!=? AND expires_at>?""", (account["id"], current, timestamp)).rowcount


def list_researchers(store):
    with store.db() as db:
        return [_public(row) for row in db.execute("SELECT * FROM researcher_accounts ORDER BY id")]


def add_researcher(store, username, password):
    name, password_hash = _credentials(username, password)
    try:
        with store.db() as db:
            cursor = db.execute("INSERT INTO researcher_accounts(username,password_hash,role) VALUES(?,?,'researcher')",
                                (name, password_hash))
            return _public(db.execute("SELECT * FROM researcher_accounts WHERE id=?", (cursor.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        raise Conflict("That username is already in use.") from None


def set_disabled(store, researcher_id, disabled):
    if type(researcher_id) is not int or not 1 < researcher_id <= 2**63 - 1 or type(disabled) is not bool:
        raise ValueError("Choose a researcher account.")
    with store.db() as db:
        changed = db.execute("UPDATE researcher_accounts SET disabled=? WHERE id=? AND role='researcher'",
                             (disabled, researcher_id))
        if not changed.rowcount:
            raise ValueError("Researcher account not found.")
        if disabled:
            db.execute("DELETE FROM researcher_logins WHERE researcher_id=?", (researcher_id,))
