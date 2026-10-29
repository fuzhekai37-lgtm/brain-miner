import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS experiments (
          id TEXT PRIMARY KEY, expression TEXT NOT NULL, settings TEXT NOT NULL,
          context TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          parent TEXT, stage INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL, result TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS notes (created TEXT, content TEXT, context TEXT NOT NULL);
        ''')

    def add(self, expression, settings, context, origin='llm', parent=None, stage=0):
        settings_text = json.dumps(settings, sort_keys=True)
        key = hashlib.sha256(json.dumps([expression.strip(), settings, context], sort_keys=True).encode()).hexdigest()
        with self.db:
            cur = self.db.execute('INSERT OR IGNORE INTO experiments(id,expression,settings,context,parent,stage,origin,created,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                (key, expression.strip(), settings_text, context, parent, stage, origin, now(), now()))
        return key if cur.rowcount else None

    def claim(self, context):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute("SELECT * FROM experiments WHERE status='pending' AND context=? ORDER BY stage,created LIMIT 1", (context,)).fetchone()
            if row:
                self.db.execute("UPDATE experiments SET status='running',updated=? WHERE id=?", (now(),row['id']))
            self.db.commit()
            return dict(row) if row else None
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, key, result=None, error=None):
        with self.db:
            self.db.execute('UPDATE experiments SET status=?,result=?,error=?,updated=? WHERE id=?',
                ('failed' if error else 'done', json.dumps(result) if result is not None else None, error, now(), key))

    def rows(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM experiments ORDER BY created')]

    def note(self, content, context):
        with self.db:
            self.db.execute('INSERT INTO notes VALUES (?,?,?)',(now(), content, context))

    def latest_note(self, context):
        row = self.db.execute('SELECT content FROM notes WHERE context=? ORDER BY rowid DESC LIMIT 1', (context,)).fetchone()
        return row[0] if row else ''

    def close(self):
        self.db.close()
