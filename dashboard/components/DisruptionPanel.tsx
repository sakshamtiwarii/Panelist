"use client";

import { useState } from "react";
import type { Company, DisruptionEvent, Room } from "@/lib/api";
import type { Clock } from "@/lib/time";

/**
 * Event composer — disruptions and roster changes.
 *
 * Events are queued rather than fired one at a time, because the injection
 * this is built for is compound — a late recruiter AND a dropped panel AND a
 * batch of withdrawals, resolved as one replan. Firing them singly would
 * produce three separate reshuffles and triple the churn.
 *
 * Roster edits sit in the same queue and travel the same path. Mechanically
 * they are identical to a disruption: both change the problem input and both
 * must be costed before they are committed. They are grouped separately only
 * because they differ in intent — one is an accident, the other a decision —
 * and a coordinator reaching for "a company pulled out" is not in the same
 * frame of mind as one reaching for "add a late registration".
 */

const DISRUPTIONS = [
  { id: "company_late", label: "Running late", hint: "company arrives late" },
  { id: "panel_drop", label: "Panel leaves", hint: "an interviewer drops" },
  { id: "student_withdraw", label: "Student out", hint: "accepted an offer" },
  { id: "room_unavailable", label: "Room lost", hint: "venue unusable" },
] as const;

const ROSTER = [
  { id: "company_add", label: "Add company", hint: "late registration" },
  { id: "company_remove", label: "Drop company", hint: "pulled out" },
  { id: "shortlist_add", label: "Add student", hint: "to a shortlist" },
  { id: "shortlist_remove", label: "Drop student", hint: "from a shortlist" },
] as const;

interface Props {
  companies: Company[];
  rooms: Room[];
  days: number;
  slots: number[];
  clock: Clock;
  queue: DisruptionEvent[];
  onQueue: (e: DisruptionEvent) => void;
  onDrop: (i: number) => void;
  describe: (e: DisruptionEvent) => string;
}

export default function DisruptionPanel({
  companies, rooms, days, slots, clock, queue, onQueue, onDrop, describe,
}: Props) {
  const [kind, setKind] = useState<string>("company_late");
  const [companyId, setCompanyId] = useState(companies[0]?.id ?? "");
  const [roomId, setRoomId] = useState(rooms[0]?.id ?? "");
  const [studentId, setStudentId] = useState("");
  const [day, setDay] = useState(0);
  const [hours, setHours] = useState(3);
  const [slot, setSlot] = useState(slots[0] ?? 0);
  const [error, setError] = useState<string | null>(null);

  // new-company fields
  const [name, setName] = useState("");
  const [cutoff, setCutoff] = useState(8.0);
  const [size, setSize] = useState(20);
  const [panels, setPanels] = useState(2);
  const [minutes, setMinutes] = useState(30);

  const add = () => {
    setError(null);
    const base: DisruptionEvent = { type: kind };

    switch (kind) {
      case "company_late":
        Object.assign(base, { company_id: companyId, day, hours });
        break;
      case "panel_drop":
        Object.assign(base, {
          company_id: companyId, count: 1, from_slot: clock.abs(day, slot),
        });
        break;
      case "student_withdraw":
        if (!studentId.trim()) return setError("Enter a student ID.");
        Object.assign(base, {
          student_id: studentId.trim().toUpperCase(),
          scope: "day",
          from_slot: clock.abs(day, slot),
        });
        break;
      case "room_unavailable":
        Object.assign(base, {
          room_id: roomId, day, from_slot: 0, to_slot: 32,
          reason: "became unavailable",
        });
        break;
      case "company_add":
        if (!name.trim()) return setError("Enter a company name.");
        Object.assign(base, {
          name: name.trim(),
          cgpa_cutoff: cutoff,
          shortlist_size: size,
          panel_count: panels,
          interview_minutes: minutes,
          tier: 3,
        });
        break;
      case "company_remove":
        Object.assign(base, { company_id: companyId });
        break;
      case "shortlist_add":
      case "shortlist_remove":
        if (!studentId.trim()) return setError("Enter a student ID.");
        Object.assign(base, {
          company_id: companyId,
          student_id: studentId.trim().toUpperCase(),
        });
        break;
    }
    onQueue(base);
  };

  const isRoster = ROSTER.some((k) => k.id === kind);
  const needsCompanyPicker =
    ["company_late", "panel_drop", "company_remove",
     "shortlist_add", "shortlist_remove"].includes(kind);
  const needsStudent =
    ["student_withdraw", "shortlist_add", "shortlist_remove"].includes(kind);
  const needsDay =
    ["company_late", "panel_drop", "student_withdraw",
     "room_unavailable"].includes(kind);
  const needsSlot = ["panel_drop", "student_withdraw"].includes(kind);

  const KindGrid = ({ items }: { items: readonly typeof DISRUPTIONS[number][] | readonly typeof ROSTER[number][] }) => (
    <div className="kind-grid">
      {items.map((k) => (
        <button
          key={k.id}
          className="kind"
          aria-pressed={kind === k.id}
          onClick={() => { setKind(k.id); setError(null); }}
        >
          {k.label}
          <small>{k.hint}</small>
        </button>
      ))}
    </div>
  );

  return (
    <>
      <div className="rail-section">
        <span className="label">1 · What happened?</span>
        <KindGrid items={DISRUPTIONS} />

        <span className="label" style={{ display: "block", margin: "14px 0 8px" }}>
          Or change the roster
        </span>
        <KindGrid items={ROSTER} />
      </div>

      <div className="rail-section">
        {needsCompanyPicker && (
          <div className="field">
            <span className="label">Company</span>
            <select
              className="select"
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
            >
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {["shortlist_add", "shortlist_remove"].includes(kind)
                    ? ` · CGPA ${c.cgpa_cutoff}+`
                    : ` · ${c.id}`}
                </option>
              ))}
            </select>
            {kind === "shortlist_add" && (
              <p className="hint" style={{ marginTop: 5 }}>
                Students below this cutoff are refused — it is a business rule,
                not a preference.
              </p>
            )}
          </div>
        )}

        {kind === "company_add" && (
          <>
            <div className="field">
              <span className="label">Company name</span>
              <input
                className="input"
                placeholder="Jane Street"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div className="field">
                <span className="label">CGPA cutoff</span>
                <input
                  className="input" type="number" step={0.1} min={5} max={10}
                  value={cutoff}
                  onChange={(e) => setCutoff(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <span className="label">Shortlist</span>
                <input
                  className="input" type="number" min={1}
                  value={size}
                  onChange={(e) => setSize(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <span className="label">Panels</span>
                <input
                  className="input" type="number" min={1} max={20}
                  value={panels}
                  onChange={(e) => setPanels(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <span className="label">Minutes</span>
                <select
                  className="select"
                  value={minutes}
                  onChange={(e) => setMinutes(Number(e.target.value))}
                >
                  {[15, 30, 45, 60].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
            </div>
            <p className="hint" style={{ marginTop: -2, marginBottom: 10 }}>
              The top {size} students at CGPA {cutoff}+ are shortlisted
              automatically.
            </p>
          </>
        )}

        {needsStudent && (
          <div className="field">
            <span className="label">Student</span>
            <input
              className="input"
              placeholder="S0272"
              value={studentId}
              onChange={(e) => setStudentId(e.target.value)}
            />
          </div>
        )}

        {kind === "room_unavailable" && (
          <div className="field">
            <span className="label">Room</span>
            <select
              className="select"
              value={roomId}
              onChange={(e) => setRoomId(e.target.value)}
            >
              {rooms.map((r) => (
                <option key={r.id} value={r.id}>{r.id} · {r.name}</option>
              ))}
            </select>
          </div>
        )}

        {needsDay && (
          <div className="field">
            <span className="label">Day</span>
            <select
              className="select"
              value={day}
              onChange={(e) => setDay(Number(e.target.value))}
            >
              {Array.from({ length: days }, (_, d) => (
                <option key={d} value={d}>Day {d + 1}</option>
              ))}
            </select>
          </div>
        )}

        {kind === "company_late" && (
          <div className="field">
            <span className="label">Hours late</span>
            <input
              className="input" type="number" min={0.25} step={0.25}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
            />
          </div>
        )}

        {needsSlot && (
          <div className="field">
            <span className="label">
              {kind === "panel_drop" ? "Leaves at" : "Offer accepted at"}
            </span>
            <select
              className="select"
              value={slot}
              onChange={(e) => setSlot(Number(e.target.value))}
            >
              {slots.map((s) => (
                <option key={s} value={s}>{clock.label(s)}</option>
              ))}
            </select>
          </div>
        )}

        {error && (
          <div className="callout err" style={{ marginBottom: 10 }}>{error}</div>
        )}

        <button className="btn" style={{ width: "100%" }} onClick={add}>
          {isRoster ? "Add this change" : "Add this event"}
        </button>
      </div>

      <div className="rail-section">
        <span className="label">2 · Queued ({queue.length})</span>
        {queue.length === 0 ? (
          <p className="hint">
            Nothing added yet. If several things happened at once, add them all
            before replanning — fixing them together moves far fewer interviews
            than fixing them one at a time.
          </p>
        ) : (
          <div className="queue">
            {queue.map((e, i) => (
              <div className="queue-item" key={i}>
                <span style={{ flex: 1 }}>{describe(e)}</span>
                <button className="x" onClick={() => onDrop(i)} aria-label="Remove">
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
