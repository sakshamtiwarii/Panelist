"""
Panelist — schedule persistence.

Two stores implement one interface. `PostgresStore` is the real one: versioned
schedules, a replan audit trail, and impact queries answered by SQL joins
rather than by loading the week into Python. `MemoryStore` is the fallback.

Why a fallback exists
---------------------
The demo must never be hostage to infrastructure. If DATABASE_URL is unset or
Postgres is unreachable, the API logs it once and keeps working in memory —
a coordinator mid-disruption should not lose the schedule because a container
died, and a live defense should not open with a connection error. `GET /health`
reports which store is active, so the degradation is visible rather than silent.
"""

import contextlib
import datetime
import json
import os
import pathlib
import threading
import time
from collections import defaultdict

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")


def open_store(url=None, quiet=False, attempts=10, delay=1.5):
    """Return a Postgres-backed store, or an in-memory one if unavailable.

    Retries before giving up: a database that is merely still booting is the
    common case on a cold start, and falling back to memory there would drop
    persistence for the whole run while every request still succeeded.
    """
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        if not quiet:
            print("[store] DATABASE_URL unset — using in-memory store")
        return MemoryStore()

    last = None
    for attempt in range(1, attempts + 1):
        try:
            store = PostgresStore(url)
            store.init_schema()
            if not quiet:
                print(f"[store] connected to Postgres ({store.describe()})")
            return store
        except Exception as e:  # driver missing, server down, bad credentials
            last = e
            if attempt < attempts:
                if not quiet and attempt == 1:
                    print(f"[store] Postgres not ready, retrying for "
                          f"{attempts * delay:.0f}s…")
                time.sleep(delay)
    if not quiet:
        print(f"[store] Postgres unavailable after {attempts} attempts "
              f"({last}) — using in-memory store")
    return MemoryStore()


class MemoryStore:
    """Process-local fallback. Same interface, no durability."""

    kind = "memory"

    def __init__(self):
        self._datasets = {}
        self._schedules = {}   # dataset -> list of version dicts
        self._replans = {}     # dataset -> list
        self._users = {}
        self._proposals = {}   # proposal_id -> {dataset, payload, expires}

    def describe(self):
        return "in-memory (not persisted)"

    def close(self):
        pass

    # -- datasets --------------------------------------------------------
    def put_dataset(self, name, ds):
        self._datasets[name] = ds

    def get_dataset(self, name):
        return self._datasets.get(name)

    def amend_dataset(self, name, ds):
        self._datasets[name] = ds

    # -- schedules -------------------------------------------------------
    def put_schedule(self, dataset, scheduled, unscheduled, report, metrics,
                     origin="solve"):
        versions = self._schedules.setdefault(dataset, [])
        entry = {
            "version": len(versions) + 1,
            "origin": origin,
            "solver_status": report.get("status") if report else None,
            "solve_seconds": report.get("wall_time_seconds") if report else None,
            "created_at": datetime.datetime.now(datetime.timezone.utc),
            "metrics": metrics,
            "scheduled": scheduled,
            "unscheduled": list(unscheduled),
        }
        versions.append(entry)
        return entry["version"]

    def get_current(self, dataset):
        versions = self._schedules.get(dataset)
        return versions[-1] if versions else None

    def versions(self, dataset):
        """Newest first, with the same keys PostgresStore returns.

        The shape and the order are part of the API contract, not an accident
        of how each store happens to hold its rows: /schedule/versions must not
        answer differently depending on whether the database was reachable.
        """
        versions = self._schedules.get(dataset, [])
        return [{
            "version": e["version"],
            "origin": e["origin"],
            "solver_status": e["solver_status"],
            "is_current": e["version"] == len(versions),
            "created_at": e["created_at"].isoformat(),
            "appointments": len(e["scheduled"]),
        } for e in reversed(versions)]

    def current_version(self, dataset):
        versions = self._schedules.get(dataset)
        return versions[-1]["version"] if versions else None

    def current_dataset(self):
        for name, versions in self._schedules.items():
            if versions:
                return name
        return None

    # -- replan audit ----------------------------------------------------
    def record_replan(self, dataset, disruptions, proposal, to_version):
        d = proposal["diff"]
        self._replans.setdefault(dataset, []).append({
            "to_version": to_version,
            "disruptions": disruptions,
            "descriptions": proposal["disruptions_applied"],
            "elective_churn": d["elective_churn_count"],
            "forced_churn": d["forced_churn_count"],
            "churn_pct": d["elective_churn_pct"],
            "cap_exceeded": bool(proposal.get("cap_exceeded")),
            "notify_count": proposal["notify"]["total_people_to_contact"],
        })

    def replan_history(self, dataset, limit=20):
        return list(reversed(self._replans.get(dataset, [])))[:limit]

    # -- proposals -------------------------------------------------------
    def put_proposal(self, pid, dataset, payload, ttl_minutes=30):
        self._proposals[pid] = {
            "dataset": dataset, "payload": payload,
            "expires": time.time() + ttl_minutes * 60,
        }

    def get_proposal(self, pid):
        row = self._proposals.get(pid)
        if row is None or row["expires"] < time.time():
            self._proposals.pop(pid, None)
            return None
        return row["payload"]

    def clear_proposals(self, dataset):
        """Drop this dataset's pending proposals, and any expired ones.

        The expired half matches PostgresStore's "OR expires_at <= now()":
        without it a long-lived process accumulates proposals for datasets it
        has moved on from, which `get_proposal` only ever clears if something
        happens to ask for them by id again.
        """
        now = time.time()
        for pid, row in list(self._proposals.items()):
            if row["dataset"] == dataset or row["expires"] < now:
                del self._proposals[pid]

    # -- users -----------------------------------------------------------
    def put_user(self, username, display_name, role, salt, password_hash):
        self._users[username] = {
            "username": username, "display_name": display_name, "role": role,
            "salt": salt, "password_hash": password_hash,
        }

    def get_user(self, username):
        return self._users.get(username)

    def touch_login(self, username):
        pass

    # -- impact ----------------------------------------------------------
    def affected(self, dataset, company_id=None, room=None, day=None):
        current = self.get_current(dataset)
        if not current:
            return []
        rows = current["scheduled"]
        hit = [
            a for a in rows
            if (company_id is None or a["company_id"] == company_id)
            and (room is None or a.get("room") == room)
            and (day is None or a["day"] == day)
        ]
        ds = self._datasets.get(dataset, {})
        cgpa = {s["id"]: s["cgpa"] for s in ds.get("students", [])}

        by_student = defaultdict(list)
        for a in hit:
            by_student[a["student_id"]].append(a)

        out = []
        for sid, mine in by_student.items():
            # "That day" means the days this student was actually hit on, not
            # the whole week. With no day filter the old code counted every
            # interview they had all week, which answers a different question
            # than the column name — and a different one than the SQL store.
            hit_days = {a["day"] for a in mine}
            same_day = sum(
                1 for a in rows
                if a["student_id"] == sid and a["day"] in hit_days
            )
            out.append({
                "student_id": sid,
                "cgpa": cgpa.get(sid),
                "interviews_hit": len(mine),
                "other_interviews_that_day": same_day - len(mine),
            })
        # Same order as PostgresStore: worst-hit first, ties by id.
        out.sort(key=lambda r: (-r["interviews_hit"], r["student_id"]))
        return out


class PostgresStore:
    """Durable, versioned schedule state."""

    kind = "postgres"

    def __init__(self, url):
        import psycopg2  # imported lazily so the driver is optional
        self._psycopg2 = psycopg2
        self.url = url
        self.conn = psycopg2.connect(url)
        self.conn.autocommit = False
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def _tx(self):
        """A transaction with exclusive use of the single connection.

        FastAPI runs sync endpoints in a threadpool, so concurrent requests
        share this store — and the dashboard deliberately fires /schedule and
        /metrics together. A psycopg2 connection is not safe for concurrent
        transactions and `with conn` is not reentrant, so without this the
        second thread dies with "the connection cannot be re-entered
        recursively" while the first succeeds: an error that appears only
        under real concurrency, and never in a sequential test.
        """
        with self._lock, self.conn, self.conn.cursor() as cur:
            yield cur

    def describe(self):
        with self._tx() as cur:
            cur.execute("select current_database()")
            return f"database {cur.fetchone()[0]}"

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def init_schema(self):
        with self._tx() as cur:
            cur.execute(SCHEMA_PATH.read_text())

    # -- datasets --------------------------------------------------------
    def put_dataset(self, name, ds):
        """Replace a dataset wholesale. Cascades wipe dependent schedules,
        which is correct: a schedule for different input data is meaningless."""
        with self._tx() as cur:
            cur.execute("DELETE FROM datasets WHERE name = %s", (name,))
            cur.execute(
                "INSERT INTO datasets (name, seed, config) VALUES (%s, %s, %s)",
                (name, ds["meta"]["seed"], json.dumps(ds["config"])),
            )
            # The disruption columns are written here as well as in
            # amend_dataset. They default to '[]', so omitting them silently
            # dropped a company's lateness windows and panel blackouts on any
            # dataset that already carried them — and MemoryStore, which keeps
            # the dict as given, disagreed with Postgres about the same input.
            cur.executemany(
                """INSERT INTO companies (dataset, id, name, tier, cgpa_cutoff,
                       panel_count, interview_minutes, duration_slots,
                       preferred_day, shortlist_size, unavailable_windows,
                       panel_blackouts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(name, c["id"], c["name"], c["tier"], c["cgpa_cutoff"],
                  c["panel_count"], c["interview_minutes"], c["duration_slots"],
                  c.get("preferred_day"), c["shortlist_size"],
                  json.dumps(c.get("unavailable_windows", [])),
                  json.dumps(c.get("panel_blackouts", [])))
                 for c in ds["companies"]],
            )
            cur.executemany(
                "INSERT INTO students (dataset, id, cgpa, branch) VALUES (%s,%s,%s,%s)",
                [(name, s["id"], s["cgpa"], s["branch"]) for s in ds["students"]],
            )
            cur.executemany(
                "INSERT INTO rooms (dataset, id, name, blocked_windows) VALUES (%s,%s,%s,%s)",
                [(name, r["id"], r["name"], json.dumps(r.get("blocked_windows", [])))
                 for r in ds["rooms"]],
            )
            pairs = [
                (name, c["id"], sid)
                for c in ds["companies"] for sid in c["shortlist"]
            ]
            cur.executemany(
                "INSERT INTO shortlists (dataset, company_id, student_id) VALUES (%s,%s,%s)",
                pairs,
            )

    def get_dataset(self, name):
        """Rebuild the solver's input dict from the tables."""
        with self._tx() as cur:
            cur.execute(
                "SELECT seed, config FROM datasets WHERE name = %s", (name,))
            row = cur.fetchone()
            if not row:
                return None
            seed, config = row

            cur.execute(
                """SELECT id, name, tier, cgpa_cutoff, panel_count,
                          interview_minutes, duration_slots, preferred_day,
                          shortlist_size, unavailable_windows, panel_blackouts
                   FROM companies WHERE dataset = %s ORDER BY id""", (name,))
            companies = [{
                "id": r[0], "name": r[1], "tier": r[2], "cgpa_cutoff": r[3],
                "panel_count": r[4], "interview_minutes": r[5],
                "duration_slots": r[6], "preferred_day": r[7],
                "shortlist_size": r[8],
                # Tuples, not lists: the model tests these as (from, to) pairs.
                "unavailable_windows": [tuple(w) for w in (r[9] or [])],
                "panel_blackouts": [tuple(w) for w in (r[10] or [])],
                "shortlist": [],
            } for r in cur.fetchall()]
            by_id = {c["id"]: c for c in companies}

            cur.execute(
                "SELECT id, cgpa, branch FROM students WHERE dataset = %s ORDER BY id",
                (name,))
            students = [{
                "id": r[0], "cgpa": r[1], "branch": r[2], "shortlisted_by": [],
            } for r in cur.fetchall()]
            s_by_id = {s["id"]: s for s in students}

            cur.execute(
                "SELECT company_id, student_id FROM shortlists WHERE dataset = %s",
                (name,))
            for cid, sid in cur.fetchall():
                if cid in by_id and sid in s_by_id:
                    by_id[cid]["shortlist"].append(sid)
                    s_by_id[sid]["shortlisted_by"].append(cid)

            cur.execute(
                "SELECT id, name, blocked_windows FROM rooms WHERE dataset = %s ORDER BY id",
                (name,))
            rooms = [{"id": r[0], "name": r[1], "blocked_windows": r[2]}
                     for r in cur.fetchall()]

        for c in companies:
            c["shortlist"].sort()
        return {
            "meta": {"seed": seed}, "config": config,
            "companies": companies, "students": students, "rooms": rooms,
        }

    def amend_dataset(self, name, ds):
        """Apply a roster change WITHOUT cascading away schedule history.

        `put_dataset` deletes and reinserts, which is right when the input data
        is genuinely replaced — but a roster amendment must keep prior schedule
        versions and the replan audit trail intact, so this upserts instead.
        Historical appointments keep their rows because `appointments.company_id`
        is deliberately not a foreign key: a past schedule should still record
        what it scheduled, even for a company that has since pulled out.
        """
        companies = ds["companies"]
        keep = [c["id"] for c in companies]
        with self._tx() as cur:
            cur.executemany(
                """INSERT INTO companies (dataset, id, name, tier, cgpa_cutoff,
                       panel_count, interview_minutes, duration_slots,
                       preferred_day, shortlist_size, unavailable_windows,
                       panel_blackouts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (dataset, id) DO UPDATE SET
                       name = EXCLUDED.name,
                       tier = EXCLUDED.tier,
                       cgpa_cutoff = EXCLUDED.cgpa_cutoff,
                       panel_count = EXCLUDED.panel_count,
                       interview_minutes = EXCLUDED.interview_minutes,
                       duration_slots = EXCLUDED.duration_slots,
                       preferred_day = EXCLUDED.preferred_day,
                       shortlist_size = EXCLUDED.shortlist_size,
                       unavailable_windows = EXCLUDED.unavailable_windows,
                       panel_blackouts = EXCLUDED.panel_blackouts""",
                [(name, c["id"], c["name"], c["tier"], c["cgpa_cutoff"],
                  c["panel_count"], c["interview_minutes"], c["duration_slots"],
                  c.get("preferred_day"), c["shortlist_size"],
                  json.dumps(c.get("unavailable_windows", [])),
                  json.dumps(c.get("panel_blackouts", [])))
                 for c in companies])

            if keep:
                cur.execute(
                    "DELETE FROM companies WHERE dataset = %s AND NOT (id = ANY(%s))",
                    (name, keep))

            # Shortlists are replaced wholesale: they are small, and diffing
            # them would be more code than rewriting them.
            cur.execute("DELETE FROM shortlists WHERE dataset = %s", (name,))
            cur.executemany(
                "INSERT INTO shortlists (dataset, company_id, student_id) VALUES (%s,%s,%s)",
                [(name, c["id"], sid) for c in companies for sid in c["shortlist"]])

            cur.executemany(
                """INSERT INTO rooms (dataset, id, name, blocked_windows)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (dataset, id) DO UPDATE SET
                       blocked_windows = EXCLUDED.blocked_windows""",
                [(name, r["id"], r["name"], json.dumps(r.get("blocked_windows", [])))
                 for r in ds["rooms"]])

    # -- schedules -------------------------------------------------------
    def put_schedule(self, dataset, scheduled, unscheduled, report, metrics,
                     origin="solve"):
        with self._tx() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM schedules WHERE dataset = %s",
                (dataset,))
            version = cur.fetchone()[0]
            # Clear the old flag first — the partial unique index enforces one
            # current schedule per dataset and would reject two.
            cur.execute(
                "UPDATE schedules SET is_current = FALSE WHERE dataset = %s AND is_current",
                (dataset,))
            cur.execute(
                """INSERT INTO schedules (dataset, version, is_current, origin,
                       solver_status, solve_seconds, metrics)
                   VALUES (%s,%s,TRUE,%s,%s,%s,%s) RETURNING id""",
                (dataset, version, origin,
                 report.get("status") if report else None,
                 report.get("wall_time_seconds") if report else None,
                 json.dumps(metrics)))
            sched_id = cur.fetchone()[0]

            cur.executemany(
                """INSERT INTO appointments (schedule_id, interview_id, company_id,
                       student_id, day, slot, start_slot, end_slot,
                       duration_slots, tier, room, panel)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(sched_id, a["id"], a["company_id"], a["student_id"], a["day"],
                  a["slot"], a["start"], a["end"], a["duration_slots"],
                  a["tier"], a.get("room"), a.get("panel")) for a in scheduled])
            if unscheduled:
                cur.executemany(
                    "INSERT INTO unscheduled (schedule_id, interview_id) VALUES (%s,%s)",
                    [(sched_id, i) for i in unscheduled])
        return version

    def get_current(self, dataset):
        with self._tx() as cur:
            cur.execute(
                """SELECT id, version, origin, solver_status, solve_seconds, metrics
                   FROM schedules WHERE dataset = %s AND is_current""", (dataset,))
            row = cur.fetchone()
            if not row:
                return None
            sched_id, version, origin, status, secs, metrics = row
            cur.execute(
                """SELECT interview_id, company_id, student_id, day, slot,
                          start_slot, end_slot, duration_slots, tier, room, panel
                   FROM appointments WHERE schedule_id = %s
                   ORDER BY start_slot, room""", (sched_id,))
            scheduled = [{
                "id": r[0], "company_id": r[1], "student_id": r[2], "day": r[3],
                "slot": r[4], "start": r[5], "end": r[6], "duration_slots": r[7],
                "tier": r[8], "room": r[9], "panel": r[10],
            } for r in cur.fetchall()]
            cur.execute(
                "SELECT interview_id FROM unscheduled WHERE schedule_id = %s",
                (sched_id,))
            unscheduled = [r[0] for r in cur.fetchall()]
        return {
            "version": version, "origin": origin, "solver_status": status,
            "solve_seconds": secs, "metrics": metrics,
            "scheduled": scheduled, "unscheduled": unscheduled,
        }

    def versions(self, dataset):
        with self._tx() as cur:
            cur.execute(
                """SELECT s.version, s.origin, s.solver_status, s.is_current,
                          s.created_at, COUNT(a.interview_id)
                   FROM schedules s
                   LEFT JOIN appointments a ON a.schedule_id = s.id
                   WHERE s.dataset = %s
                   GROUP BY s.id ORDER BY s.version DESC""", (dataset,))
            return [{
                "version": r[0], "origin": r[1], "solver_status": r[2],
                "is_current": r[3], "created_at": r[4].isoformat(),
                "appointments": r[5],
            } for r in cur.fetchall()]

    def current_version(self, dataset):
        """Version of the live schedule, or None.

        One indexed row. Exists so a caller can ask "has anything changed"
        without loading a thousand appointments to find out — which is what
        checking `get_current()` for the same answer costs.
        """
        with self._tx() as cur:
            cur.execute(
                "SELECT version FROM schedules WHERE dataset = %s AND is_current",
                (dataset,))
            row = cur.fetchone()
        return row[0] if row else None

    def current_dataset(self):
        """The dataset that has a live schedule, or None.

        Lets a freshly-started worker find the current dataset instead of
        depending on having served the request that solved it.
        """
        with self._tx() as cur:
            cur.execute("SELECT dataset FROM schedules WHERE is_current LIMIT 1")
            row = cur.fetchone()
        return row[0] if row else None

    # -- replan audit ----------------------------------------------------
    def record_replan(self, dataset, disruptions, proposal, to_version):
        d = proposal["diff"]
        with self._tx() as cur:
            cur.execute(
                "SELECT id FROM schedules WHERE dataset = %s AND version = %s",
                (dataset, to_version))
            row = cur.fetchone()
            to_id = row[0] if row else None
            # The version this replan built on. Clamping to 1 would make the
            # first row claim it went from v1 to v1; there is simply no prior
            # schedule to point at in that case.
            from_id = None
            if to_version > 1:
                cur.execute(
                    "SELECT id FROM schedules WHERE dataset = %s AND version = %s",
                    (dataset, to_version - 1))
                row = cur.fetchone()
                from_id = row[0] if row else None
            cur.execute(
                """INSERT INTO replan_events (dataset, from_schedule, to_schedule,
                       disruptions, descriptions, elective_churn, forced_churn,
                       churn_pct, cap_exceeded, notify_count, diff, notify)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (dataset, from_id, to_id,
                 json.dumps(disruptions),
                 json.dumps(proposal["disruptions_applied"]),
                 d["elective_churn_count"], d["forced_churn_count"],
                 d["elective_churn_pct"], bool(proposal.get("cap_exceeded")),
                 proposal["notify"]["total_people_to_contact"],
                 json.dumps({k: d[k] for k in (
                     "added", "removed", "moved", "forced_removed",
                     "elective_removed", "affected_students",
                     "affected_companies")}),
                 json.dumps(proposal["notify"])))

    def replan_history(self, dataset, limit=20):
        with self._tx() as cur:
            cur.execute(
                """SELECT applied_at, descriptions, elective_churn, forced_churn,
                          churn_pct, cap_exceeded, notify_count, to_schedule
                   FROM replan_events WHERE dataset = %s
                   ORDER BY applied_at DESC LIMIT %s""", (dataset, limit))
            return [{
                "applied_at": r[0].isoformat(), "descriptions": r[1],
                "elective_churn": r[2], "forced_churn": r[3],
                "churn_pct": r[4], "cap_exceeded": r[5],
                "notify_count": r[6], "schedule_id": r[7],
            } for r in cur.fetchall()]

    # -- proposals -------------------------------------------------------
    def put_proposal(self, pid, dataset, payload, ttl_minutes=30):
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO proposals (id, dataset, payload, expires_at)
                   VALUES (%s, %s, %s, now() + make_interval(mins => %s))""",
                (pid, dataset, json.dumps(payload), ttl_minutes))

    def get_proposal(self, pid):
        """The stored proposal, or None if unknown or expired."""
        with self._tx() as cur:
            cur.execute(
                "SELECT payload FROM proposals WHERE id = %s AND expires_at > now()",
                (pid,))
            row = cur.fetchone()
        return row[0] if row else None

    def clear_proposals(self, dataset):
        """Drop every pending proposal for a dataset.

        Called after one is applied: the rest were computed against a schedule
        that no longer exists, so applying them would silently write a plan
        built from stale state. Expired rows go at the same time.
        """
        with self._tx() as cur:
            cur.execute(
                "DELETE FROM proposals WHERE dataset = %s OR expires_at <= now()",
                (dataset,))

    # -- users -----------------------------------------------------------
    def put_user(self, username, display_name, role, salt, password_hash):
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO users (username, display_name, role, salt, password_hash)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (username) DO UPDATE
                     SET display_name = EXCLUDED.display_name,
                         role = EXCLUDED.role,
                         salt = EXCLUDED.salt,
                         password_hash = EXCLUDED.password_hash""",
                (username, display_name, role,
                 self._psycopg2.Binary(salt),
                 self._psycopg2.Binary(password_hash)))

    def get_user(self, username):
        with self._tx() as cur:
            cur.execute(
                """SELECT username, display_name, role, salt, password_hash
                   FROM users WHERE username = %s""", (username,))
            r = cur.fetchone()
        if not r:
            return None
        return {"username": r[0], "display_name": r[1], "role": r[2],
                "salt": bytes(r[3]), "password_hash": bytes(r[4])}

    def touch_login(self, username):
        with self._tx() as cur:
            cur.execute(
                "UPDATE users SET last_login = now() WHERE username = %s",
                (username,))

    # -- impact ----------------------------------------------------------
    def affected(self, dataset, company_id=None, room=None, day=None):
        """Who a disruption touches, and what else they have that day.

        This is the query the guide names as the reason to have a database:
        the second column is a correlated count over the same student's other
        interviews, which is a join, not a lookup.
        """
        clauses, params = ["s.dataset = %s", "s.is_current"], [dataset]
        if company_id:
            clauses.append("a.company_id = %s")
            params.append(company_id)
        if room:
            clauses.append("a.room = %s")
            params.append(room)
        if day is not None:
            clauses.append("a.day = %s")
            params.append(day)

        sql = f"""
            WITH hit AS (
                SELECT a.schedule_id, a.student_id, a.day, COUNT(*) AS n
                FROM appointments a
                JOIN schedules s ON s.id = a.schedule_id
                WHERE {' AND '.join(clauses)}
                GROUP BY a.schedule_id, a.student_id, a.day
            ),
            -- Per (student, day) hit: what ELSE that student has that same
            -- day. Kept as its own step so the correlated count stays scoped
            -- to the day, while the final grouping is per student.
            per_day AS (
                SELECT h.student_id, h.n,
                       (SELECT COUNT(*) FROM appointments o
                         WHERE o.schedule_id = h.schedule_id
                           AND o.student_id  = h.student_id
                           AND o.day         = h.day) - h.n AS others
                FROM hit h
            )
            -- One row per student. Grouping by h.day as well used to emit a
            -- row per student-day, so an unfiltered query reported more
            -- students affected than there were students.
            SELECT p.student_id,
                   st.cgpa,
                   SUM(p.n)::int      AS interviews_hit,
                   SUM(p.others)::int AS other_interviews_that_day
            FROM per_day p
            JOIN students st ON st.dataset = %s AND st.id = p.student_id
            GROUP BY p.student_id, st.cgpa
            ORDER BY interviews_hit DESC, p.student_id
        """
        with self._tx() as cur:
            cur.execute(sql, params + [dataset])
            return [{
                "student_id": r[0], "cgpa": r[1],
                "interviews_hit": r[2], "other_interviews_that_day": r[3],
            } for r in cur.fetchall()]
