"""Rolling hourly and daily caps on scraped contacts, kept in SQLite so they survive restarts."""
import os
import sqlite3
import time

HOURLY_CONTACT_LIMIT = int(os.environ.get("HOURLY_CONTACT_LIMIT", "100"))
DAILY_CONTACT_LIMIT = int(os.environ.get("DAILY_CONTACT_LIMIT", "1000"))
WINDOWS = (
    ("hour", 3600, HOURLY_CONTACT_LIMIT),
    ("24 hours", 86400, DAILY_CONTACT_LIMIT),
)


class ContactQuota:
    def __init__(self, path):
        self.path = path
        self._db = None

    @property
    def db(self):
        if self._db is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._db = sqlite3.connect(self.path)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS usage (ts REAL NOT NULL, job_id TEXT NOT NULL, contacts INTEGER NOT NULL)"
            )
            self._db.commit()
        return self._db

    def _used(self, since):
        return self.db.execute("SELECT COALESCE(SUM(contacts), 0) FROM usage WHERE ts > ?", (since,)).fetchone()[0]

    def remaining(self):
        """Contacts still allowed now, and if none, a message saying which limit is hit and until when."""
        now = time.time()
        left, reason, longest_wait = None, None, -1
        for label, window, limit in WINDOWS:
            window_left = max(0, limit - self._used(now - window))
            if window_left == 0:
                oldest = self.db.execute("SELECT MIN(ts) FROM usage WHERE ts > ?", (now - window,)).fetchone()[0]
                wait = int((oldest or now) + window - now) // 60 + 1
                if wait > longest_wait:  # name the limit that blocks longest
                    longest_wait = wait
                    when = f"{wait} minutes" if wait < 120 else f"{round(wait / 60)} hours"
                    reason = f"Limit reached: {limit} contacts per {label}. More become available in about {when}."
            left = window_left if left is None else min(left, window_left)
        return left, reason

    def charged(self, job_id):
        return self.db.execute("SELECT COALESCE(SUM(contacts), 0) FROM usage WHERE job_id = ?", (job_id,)).fetchone()[0]

    def charge(self, job_id, contacts):
        self.db.execute("INSERT INTO usage VALUES (?, ?, ?)", (time.time(), job_id, contacts))
        self.db.commit()

    def summary(self):
        now = time.time()
        return "Usage: " + ", ".join(
            f"{self._used(now - window)}/{limit} contacts in the last {label}" for label, window, limit in WINDOWS
        ) + "."
