"use client";

import { useState } from "react";
import type { Company, DisruptionEvent, Room } from "@/lib/api";
import type { Clock } from "@/lib/time";

/**
 * Disruption composer.
 *
 * Events are queued rather than fired one at a time, because the injection
 * this is built for is compound — a late recruiter AND a dropped panel AND a
 * batch of withdrawals, resolved as one replan. Firing them singly would
 * produce three separate reshuffles and triple the churn.
 */

const KINDS = [
  { id: "company_late", label: "Running late", hint: "company arrives late" },
  { id: "panel_drop", label: "Panel leaves", hint: "an interviewer drops" },
  { id: "student_withdraw", label: "Student out", hint: "accepted an offer" },
  { id: "room_unavailable", label: "Room lost", hint: "venue unusable" },
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

  const add = () => {
    const base: DisruptionEvent = { type: kind };
    if (kind === "company_late") {
      Object.assign(base, { company_id: companyId, day, hours });
    } else if (kind === "panel_drop") {
      Object.assign(base, {
        company_id: companyId, count: 1, from_slot: clock.abs(day, slot),
      });
    } else if (kind === "student_withdraw") {
      if (!studentId.trim()) return;
      Object.assign(base, {
        student_id: studentId.trim().toUpperCase(),
        scope: "day",
        from_slot: clock.abs(day, slot),
      });
    } else {
      Object.assign(base, {
        room_id: roomId, day, from_slot: 0, to_slot: 32,
        reason: "became unavailable",
      });
    }
    onQueue(base);
  };

  const needsCompany = kind === "company_late" || kind === "panel_drop";
  const needsSlot = kind === "panel_drop" || kind === "student_withdraw";

  return (
    <>
      <div className="rail-section">
        <span className="label">1 · What went wrong?</span>
        <div className="kind-grid">
          {KINDS.map((k) => (
            <button
              key={k.id}
              className="kind"
              aria-pressed={kind === k.id}
              onClick={() => setKind(k.id)}
            >
              {k.label}
              <small>{k.hint}</small>
            </button>
          ))}
        </div>
      </div>

      <div className="rail-section">
        {needsCompany && (
          <div className="field">
            <span className="label">Company</span>
            <select
              className="select"
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
            >
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} · {c.name}
                </option>
              ))}
            </select>
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

        {kind === "student_withdraw" && (
          <div className="field">
            <span className="label">Student</span>
            <input
              className="input"
              placeholder="S0027"
              value={studentId}
              onChange={(e) => setStudentId(e.target.value)}
            />
          </div>
        )}

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

        {kind === "company_late" && (
          <div className="field">
            <span className="label">Hours late</span>
            <input
              className="input"
              type="number"
              min={0.25}
              step={0.25}
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

        <button className="btn" style={{ width: "100%" }} onClick={add}>
          Add this event
        </button>
      </div>

      <div className="rail-section">
        <span className="label">2 · Events to fix ({queue.length})</span>
        {queue.length === 0 ? (
          <p className="hint">
            Nothing added yet. If several things went wrong at once, add them
            all before replanning — fixing them together moves far fewer
            interviews than fixing them one at a time.
          </p>
        ) : (
          <div className="queue">
            {queue.map((e, i) => (
              <div className="queue-item" key={i}>
                <span style={{ flex: 1 }}>{describe(e)}</span>
                <button
                  className="x"
                  onClick={() => onDrop(i)}
                  aria-label="Remove"
                >
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
