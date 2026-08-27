"use client";

import { useMemo } from "react";
import type { Appointment, ConfigResponse, Room } from "@/lib/api";
import type { Clock } from "@/lib/time";

/**
 * Room x time board for one day.
 *
 * A table of appointments would be easier to build and much harder to read.
 * A coordinator's questions are spatial — "what's free at 2pm", "how bad is
 * the 11am crunch", "what does this delay push into" — and a grid answers
 * them without the reader assembling anything in their head.
 *
 * Appointments are absolutely positioned over the cell matrix so an interview
 * spanning three slots reads as one block rather than three stacked rows.
 */

export type ChangeState = "moved" | "added" | "cut" | null;

interface Props {
  day: number;
  cfg: ConfigResponse;
  clock: Clock;
  appointments: Appointment[];
  companyName: (id: string) => string;
  changeState: (id: string) => ChangeState;
  lockedBefore: number | null;
  focusStudent: string | null;
  onPick: (a: Appointment) => void;
}

const ROW_H = 27;
const COL_W = 78;
const GUTTER = 62;
const HEAD_H = 30;

// Status carries a glyph as well as a hue, so a change stays readable in
// greyscale and to colour-blind readers (the palette rule: never colour alone).
const MARK: Record<string, string> = { moved: "\u21C5", added: "+", cut: "\u00D7" };

export default function ScheduleGrid({
  day, cfg, clock, appointments, companyName,
  changeState, lockedBefore, focusStudent, onPick,
}: Props) {
  const rooms = cfg.rooms;
  const slots = cfg.config.usable_slots_per_day;

  // Row index per slot, since lunch makes slot numbers non-contiguous.
  const rowOf = useMemo(() => {
    const m = new Map<number, number>();
    slots.forEach((s, i) => m.set(s, i));
    return m;
  }, [slots]);

  const roomCol = useMemo(() => {
    const m = new Map<string, number>();
    rooms.forEach((r, i) => m.set(r.id, i));
    return m;
  }, [rooms]);

  const blockedCells = useMemo(() => {
    const set = new Set<string>();
    rooms.forEach((r) =>
      r.blocked_windows
        .filter((w) => w.day === day)
        .forEach((w) => {
          for (let s = w.from_slot; s < w.to_slot; s++) set.add(`${r.id}:${s}`);
        }),
    );
    return set;
  }, [rooms, day]);

  const roomBlockedToday = useMemo(() => {
    const s = new Set<string>();
    rooms.forEach((r) => {
      if (r.blocked_windows.some((w) => w.day === day)) s.add(r.id);
    });
    return s;
  }, [rooms, day]);

  const todays = appointments.filter((a) => a.day === day && a.room);

  return (
    <div className="grid-scroll">
      <div
        className="grid"
        style={{
          gridTemplateColumns: `${GUTTER}px repeat(${rooms.length}, ${COL_W}px)`,
          gridTemplateRows: `${HEAD_H}px repeat(${slots.length}, ${ROW_H}px)`,
        }}
      >
        <div className="grid-corner" />
        {rooms.map((r) => (
          <div
            key={r.id}
            className={`grid-roomhead${roomBlockedToday.has(r.id) ? " blocked" : ""}`}
            title={blockedReason(r, day)}
          >
            {r.name.replace(/^Room /, "R")}
          </div>
        ))}

        {slots.map((s, row) => (
          <TimeRow
            key={s}
            slot={s}
            row={row}
            rooms={rooms}
            clock={clock}
            blockedCells={blockedCells}
          />
        ))}

        {todays.map((a) => {
          const row = rowOf.get(a.slot);
          const col = roomCol.get(a.room!);
          if (row === undefined || col === undefined) return null;

          // Duration can run past lunch in row terms; clamp to the visible run.
          const endRow = rowOf.get(a.slot + a.duration_slots - 1) ?? row;
          const span = Math.max(1, endRow - row + 1);
          const state = changeState(a.id);
          const locked =
            lockedBefore !== null && clock.abs(a.day, a.slot) < lockedBefore;
          const dim = focusStudent !== null && a.student_id !== focusStudent;

          return (
            <button
              key={a.id}
              className={[
                "appt",
                `tier-${a.tier}`,
                state ? `is-${state}` : "",
                locked ? "is-locked" : "",
                dim ? "dimmed" : "",
              ].filter(Boolean).join(" ")}
              style={{
                top: HEAD_H + row * ROW_H + 1,
                left: GUTTER + col * COL_W + 1,
                width: COL_W - 3,
                height: span * ROW_H - 3,
              }}
              onClick={() => onPick(a)}
              title={[
                companyName(a.company_id),
                a.student_id,
                `${clock.label(a.slot)}–${clock.label(a.slot + a.duration_slots)}`,
                `${a.room} · panel ${a.panel}`,
                locked ? "already under way — locked" : "",
              ].filter(Boolean).join("\n")}
            >
              {state && <span className="mark">{MARK[state]}</span>}
              <span className="co">{shortName(companyName(a.company_id))}</span>
              {span > 1 && <span className="st">{a.student_id}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TimeRow({
  slot, row, rooms, clock, blockedCells,
}: {
  slot: number; row: number; rooms: Room[]; clock: Clock;
  blockedCells: Set<string>;
}) {
  const hour = clock.isHour(slot);
  return (
    <>
      <div
        className={`grid-time${hour ? " hour" : ""}`}
        style={{ gridRow: row + 2, gridColumn: 1 }}
      >
        {hour ? clock.label(slot) : ""}
      </div>
      {rooms.map((r, i) => (
        <div
          key={r.id}
          className={[
            "grid-cell",
            hour ? "hour" : "",
            blockedCells.has(`${r.id}:${slot}`) ? "blocked" : "",
          ].filter(Boolean).join(" ")}
          style={{ gridRow: row + 2, gridColumn: i + 2 }}
        />
      ))}
    </>
  );
}

function blockedReason(room: Room, day: number) {
  const w = room.blocked_windows.find((x) => x.day === day);
  return w ? `${room.name} — ${w.reason}` : room.name;
}

/** "Northwind Labs" -> "Northwind". Cells are 60px; the suffix never fits. */
function shortName(name: string) {
  return name.replace(/ (Labs|Systems|Corp|Tech)$/, "");
}
