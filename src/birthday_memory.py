"""Stable Discord identities and evidence-backed birthday candidates, not predictions.

All identities are (guild_id, user_id). Names are non-exclusive observed aliases.
Historical message authors can have present-day names; observed_at is NOT the
message timestamp. No model calls, birth-year inference, or account-link guesses.
"""
from __future__ import annotations

import calendar
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BIRTHDAY_WORD = re.compile(r"\b(?:birthday|b[ -]?day|hbd)\b", re.I)
MENTION = re.compile(r"<@!?(\d+)>")
SELF = re.compile(r"\b(?:my\s+(?:birthday|b[ -]?day)|happy\s+birthday\s+to\s+me)\b", re.I)
GREETING = re.compile(
    r"^(?:<@!?\d+>\s*[,!:]?\s*)?(?:happy\s+(?:(?:belated|early)\s+)?"
    r"(?:\d{1,3}(?:st|nd|rd|th)\s+)?(?:birthday|b[ -]?day)|hbd)\b", re.I
)
MONTHS = {name.casefold(): i for i in range(1, 13)
          for name in (calendar.month_name[i], calendar.month_abbr[i])}
MONTHS['sept'] = 9
MONTH_PATTERN = '|'.join(sorted(MONTHS, key=len, reverse=True))
MONTH_DAY = re.compile(rf"\b({MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})\b", re.I)


def _norm(value: str) -> str:
    return ' '.join(value.casefold().split())


def _month_day(text: str):
    iso = re.search(r'\b\d{4}-(\d{2})-(\d{2})\b', text)
    forward, reverse = MONTH_DAY.search(text), DAY_MONTH.search(text)
    if iso:
        result = tuple(map(int, iso.groups()))
    elif forward:
        result = MONTHS[forward[1].casefold()], int(forward[2])
    elif reverse:
        result = MONTHS[reverse[2].casefold()], int(reverse[1])
    else:
        return None
    try:
        date(2000, *result)  # Leap-day birthdays are legitimate.
    except ValueError:
        return None
    return result


def parse_birthday(row: dict, zone: ZoneInfo):
    """Return candidate attributes. Ambiguity remains null, never silently guessed."""
    text = (row.get('content') or '').strip()[:4000]
    if not BIRTHDAY_WORD.search(text) or text.startswith(('>', '`', '"', '“', "'")):
        return None
    self_match = SELF.search(text)
    greeting = GREETING.match(text)
    if self_match and re.search(r'\b(?:said|says|quote|if|pretend|joking)\b', text[:self_match.start()], re.I):
        self_match = None
    if not self_match and not greeting:
        return None
    subject, alias = None, ''
    kind = 'self' if self_match else 'greeting'
    if self_match:
        subject = int(row['user_id'])
        # Prefer the clause after "my birthday" over later correction text.
        date_text = text[self_match.end():]
    else:
        mentions = {int(x) for x in MENTION.findall(text)} - set(row.get('bot_ids', ()))
        if len(mentions) == 1:
            subject = mentions.pop()
        elif not mentions and row.get('reply_subject_id'):
            subject = int(row['reply_subject_id'])
            kind = 'reply'
        elif not mentions:
            alias = re.sub(r'^\s*(?:to\s+)?', '', text[greeting.end():], flags=re.I)
            alias = re.split(r'[!?,.\n]', alias, maxsplit=1)[0].strip()[:100]
            # Store a name only as a name. It will be resolved afresh on retrieval.
            if re.search(r'\b(?:and|everyone|all|tomorrow|yesterday|today|belated|early)\b', alias, re.I):
                alias = ''
            kind = 'alias'
        date_text = text
    if self_match and (re.search(r'\bnot\s*$', text[:self_match.start()], re.I)
                       or re.match(r"\s*(?:is\s+not|was\s+not|isn['’]?t|wasn['’]?t|not)\b", date_text, re.I)):
        kind = 'denial'
    explicit = _month_day(date_text)
    stamp = datetime.fromtimestamp(float(row['created_at']), timezone.utc).astimezone(zone)
    relative = re.search(r'\b(today|tomorrow|yesterday)\b', text, re.I)
    uncertain_date = re.search(r'\b(?:belated|early|late|next|last|week|month|not|isnt|isn.t)\b', date_text, re.I)
    if explicit:
        month, day = explicit
        note = 'explicit date in source'
    elif relative:
        shifted = stamp.date() + timedelta(days={'today': 0, 'tomorrow': 1, 'yesterday': -1}[relative[1].lower()])
        month, day = shifted.month, shifted.day
        note = 'relative date interpreted in configured timezone'
    elif uncertain_date or re.search(r'\d\s*[/.-]\s*\d', date_text):
        month = day = None
        note = 'date ambiguous; not the posting date by assumption'
    elif self_match and not re.search(r"(?:it['’]?s|today\s+is|happy\s+birthday\s+to\s+me)", text, re.I):
        month = day = None
        note = 'self-reference without an unambiguous date'
    else:
        month, day = stamp.month, stamp.day
        note = 'posting-day inference; timezone and delayed wishes may differ'
    return subject, alias, month, day, kind, note


class BirthdayStore:
    def __init__(self, path: str, timezone_name: str = 'America/Los_Angeles'):
        self.path = str(path)
        self.zone = ZoneInfo(timezone_name)
        if self.path == ':memory:':
            raise ValueError('BirthdayStore needs a persistent file')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS member_identities (
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL, observed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id,user_id));
                CREATE TABLE IF NOT EXISTS member_aliases (
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    alias TEXT NOT NULL, normalized TEXT NOT NULL,
                    first_observed REAL NOT NULL, last_observed REAL NOT NULL,
                    PRIMARY KEY (guild_id,user_id,normalized));
                CREATE INDEX IF NOT EXISTS member_alias_lookup
                    ON member_aliases(guild_id,normalized);
                CREATE TABLE IF NOT EXISTS birthday_candidates (
                    message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL, observer_id INTEGER NOT NULL,
                    subject_id INTEGER, target_alias TEXT NOT NULL,
                    month INTEGER, day INTEGER, kind TEXT NOT NULL,
                    note TEXT NOT NULL, created_at REAL NOT NULL, excerpt TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS birthday_candidate_scope
                    ON birthday_candidates(guild_id,channel_id,subject_id);
                CREATE TABLE IF NOT EXISTS birthday_scan_progress (
                    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                    before_id INTEGER, complete INTEGER NOT NULL DEFAULT 0,
                    scanned INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    PRIMARY KEY (guild_id,channel_id));
                CREATE TABLE IF NOT EXISTS birthday_optouts (
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id,user_id));
            ''')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        return db

    @staticmethod
    def _identity(db, guild_id, user_id, names, observed_at):
        names = list(dict.fromkeys(str(x).strip()[:100] for x in names if x and str(x).strip()))
        if not names:
            return
        db.execute('''INSERT INTO member_identities VALUES (?,?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET
            display_name=excluded.display_name, observed_at=excluded.observed_at
            WHERE excluded.observed_at >= member_identities.observed_at''',
            (guild_id, user_id, names[0], observed_at))
        db.executemany('''INSERT INTO member_aliases VALUES (?,?,?,?,?,?)
            ON CONFLICT(guild_id,user_id,normalized) DO UPDATE SET
            last_observed=MAX(member_aliases.last_observed,excluded.last_observed)''',
            [(guild_id, user_id, name, _norm(name), observed_at, observed_at) for name in names])

    def observe_identity(self, guild_id, user_id, names, observed_at=None):
        with closing(self._connect()) as db, db:
            self._identity(db, guild_id, user_id, names, time.time() if observed_at is None else observed_at)

    @staticmethod
    def _aliases(db, guild_id):
        values = defaultdict(set)
        for row in db.execute('SELECT normalized,user_id FROM member_aliases WHERE guild_id=?', (guild_id,)):
            values[row['normalized']].add(row['user_id'])
        return {name: next(iter(ids)) for name, ids in values.items() if len(ids) == 1}

    def resolve_alias(self, guild_id, name):
        with closing(self._connect()) as db:
            return self._aliases(db, guild_id).get(_norm(name))

    def current_name(self, guild_id, user_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT display_name FROM member_identities WHERE guild_id=? AND user_id=?',
                             (guild_id, user_id)).fetchone()
            return row[0] if row else f'user {user_id}'

    def ingest(self, rows, *, checkpoint=None):
        """Idempotent page import; candidates and cursor commit in one transaction."""
        added = 0
        with closing(self._connect()) as db, db:
            for row in rows:
                guild_id = int(row['guild_id'])
                observed = row.get('observed_at', time.time())
                for uid, names in row.get('identities', ()):
                    self._identity(db, guild_id, int(uid), names, observed)
                parsed = parse_birthday(row, self.zone)
                db.execute('DELETE FROM birthday_candidates WHERE guild_id=? AND message_id=?',
                           (guild_id, int(row['message_id'])))
                if parsed is None:
                    continue
                subject, alias, month, day, kind, note = parsed
                if subject is not None and db.execute('SELECT 1 FROM birthday_optouts WHERE guild_id=? AND user_id=?',
                                                      (guild_id, subject)).fetchone():
                    continue
                db.execute('INSERT INTO birthday_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (int(row['message_id']), guild_id, int(row['channel_id']), int(row['user_id']),
                            subject, alias, month, day, kind, note, float(row['created_at']), row['content'][:1200]))
                added += 1
            if checkpoint is not None:
                guild_id, channel_id, before_id, complete, count = checkpoint
                db.execute('''INSERT INTO birthday_scan_progress VALUES (?,?,?,?,?,?)
                    ON CONFLICT(guild_id,channel_id) DO UPDATE SET before_id=excluded.before_id,
                    complete=excluded.complete, scanned=birthday_scan_progress.scanned+excluded.scanned,
                    updated_at=excluded.updated_at''', (guild_id, channel_id, before_id, int(complete), count, time.time()))
        return added

    def progress(self, guild_id, channel_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM birthday_scan_progress WHERE guild_id=? AND channel_id=?',
                             (guild_id, channel_id)).fetchone()
            return dict(row) if row else dict(before_id=None, complete=0, scanned=0)

    def summary(self, guild_id, channel_ids, user_ids=()):
        channels = list(dict.fromkeys(int(x) for x in channel_ids))[:900]
        if not channels:
            return []
        with closing(self._connect()) as db:
            rows = db.execute('SELECT * FROM birthday_candidates WHERE guild_id=? AND channel_id IN ('
                              + ','.join('?' for _ in channels) + ') ORDER BY created_at DESC',
                              (guild_id, *channels)).fetchall()
            aliases = self._aliases(db, guild_id)
            names = dict(db.execute('SELECT user_id,display_name FROM member_identities WHERE guild_id=?', (guild_id,)))
            opted_out = {r[0] for r in db.execute('SELECT user_id FROM birthday_optouts WHERE guild_id=?', (guild_id,))}
        groups = {}
        dates = defaultdict(set)
        denied = set()
        for row in rows:
            subject = row['subject_id'] or aliases.get(_norm(row['target_alias']))
            if not subject or subject in opted_out or (user_ids and subject not in user_ids) or row['month'] is None:
                continue
            key = subject, row['month'], row['day']
            if row['kind'] == 'denial':
                denied.add(key)
                continue
            dates[subject].add(key[1:])
            group = groups.setdefault(key, dict(user_id=subject, name=names.get(subject, f'user {subject}'),
                month=row['month'], day=row['day'], observers=set(), years=set(), signals=0,
                self_report=False, alias_only=True, sources=[]))
            group['observers'].add(row['observer_id'])
            group['years'].add(datetime.fromtimestamp(row['created_at'], timezone.utc).astimezone(self.zone).year)
            group['signals'] += 1
            group['self_report'] |= row['kind'] == 'self'
            group['alias_only'] &= row['kind'] == 'alias'
            if len(group['sources']) < 3:
                group['sources'].append(f"https://discord.com/channels/{guild_id}/{row['channel_id']}/{row['message_id']}")
        result = []
        for group in groups.values():
            group['observers'], group['years'] = len(group['observers']), len(group['years'])
            status = 'tentative'
            if group['self_report']:
                status = 'self-reported'
            elif group['years'] >= 2 and group['observers'] >= 3 and not group['alias_only']:
                status = 'repeated pattern, not confirmed'
            if group['alias_only']:
                status = 'tentative name match'
            if len(dates[group['user_id']]) > 1 or (group['user_id'], group['month'], group['day']) in denied:
                status = 'conflicting evidence'
            group['status'] = status
            result.append(group)
        return sorted(result, key=lambda x: (x['month'], x['day'], x['user_id']))

    def stats(self, guild_id):
        with closing(self._connect()) as db:
            candidates = db.execute('SELECT COUNT(*) FROM birthday_candidates WHERE guild_id=?', (guild_id,)).fetchone()[0]
            scanned, complete, channels = db.execute('SELECT COALESCE(SUM(scanned),0), COALESCE(SUM(complete),0),COUNT(*) '
                'FROM birthday_scan_progress WHERE guild_id=?', (guild_id,)).fetchone()
            aliases = self._aliases(db, guild_id)
            unresolved = sum(1 for row in db.execute('SELECT subject_id,target_alias,month FROM birthday_candidates '
                'WHERE guild_id=?', (guild_id,)) if row['month'] is None or not (row['subject_id'] or aliases.get(_norm(row['target_alias']))))
            return dict(candidates=candidates, unresolved=unresolved, scanned=scanned, complete=complete, channels=channels)

    def delete_message(self, guild_id, message_id):
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM birthday_candidates WHERE guild_id=? AND message_id=?', (guild_id, message_id))

    def forget(self, guild_id, user_id):
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO birthday_optouts VALUES (?,?)', (guild_id, user_id))
            db.execute('DELETE FROM birthday_candidates WHERE guild_id=? AND (subject_id=? OR observer_id=?)',
                       (guild_id, user_id, user_id))

    def context(self, guild_id, channel_ids, user_ids=(), limit=12):
        rows = self.summary(guild_id, channel_ids, user_ids)[:max(1, min(limit, 30))]
        if not rows:
            return ''
        lines = ['Birthday evidence, not a prediction. Identity is the numeric user ID; names are labels.',
                 'Wishes may be late, joking, or in another timezone. Conflicts reduce certainty; no percentages are calibrated.']
        for r in rows:
            label = re.sub(r'[\r\n\x00]', ' ', r['name'])[:100]
            lines.append(f"- {label} (user {r['user_id']}): candidate {r['month']:02d}-{r['day']:02d}; {r['status']}; "
                         f"{r['signals']} sources, {r['observers']} authors, {r['years']} calendar years. Source: {r['sources'][0]}")
        return '\n'.join(lines)[:6000]
