"use client";

import { useMemo } from "react";
import type { Appointment, ConfigResponse, Room } from "@/lib/api";
import type { Clock } from "@/lib/time";

/**
 * Room x time board for one day.
 *
 * Appointments are absolutely positioned over the cell matrix, so an interview
 * spanning three slots reads as one block rather than three stacked rows.
 *
 * Rooms are zebra-striped to keep a column trackable across twenty of them,
 * lunch gets a row of its own (the usable-slot list omits it, so without one
 * 12:45 appears to run straight into 14:00), and the current time is drawn as
 * a labelled rule.
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
  /** Narrow the board to one room. Null shows every room. */
  roomFilter: string | null;
  /** Dim everything except one company. Null dims nothing. */
  companyFilter: string | null;
  /** Per-room utilisation %, keyed by room id. */
  roomUtilisation?: Record<string, number>;
  /** Geometry, so the toolbar's density switch reaches the absolutely
      positioned blocks as well as the CSS grid. If the two disagree, every
      block floats off its cell. */
  rowH: number;
  colW: number;
  gutter: number;
}

const HEAD_H = 34;
const BREAK_H = 20;

// A glyph as well as a hue, so a change stays readable in greyscale.
const MARK: Record<string, string> = { moved: "⇅", added: "+", cut: "×" };

export default function ScheduleGrid({
  day, cfg, clock, appointments, companyName,
  changeState, lockedBefore, focusStudent, onPick,
  roomFilter, companyFilter, roomUtilisation,
  rowH, colW, gutter,
}: Props) {
  const rooms = useMemo(
    () => (roomFilter ? cfg.rooms.filter((r) => r.id === roomFilter) : cfg.rooms),
    [cfg.rooms, roomFilter],
  );
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

  // The last row before the slot numbers jump — that gap is lunch. Null when
  // the day happens to have no break at all.
  const breakAfter = useMemo(() => {
    for (let i = 1; i < slots.length; i++) {
      if (slots[i] !== slots[i - 1] + 1) return i - 1;
    }
    return null;
  }, [slots]);

  /* Two coordinate systems have to agree: CSS grid lines for the cells, pixel
     offsets for the absolutely positioned blocks. Both derive from `past` so a
     lunch row cannot shift one without the other. */
  const past = (row: number) => breakAfter !== null && row > breakAfter;
  const gridRowOf = (row: number) => row + 2 + (past(row) ? 1 : 0);
  const yOf = (row: number) => HEAD_H + row * rowH + (past(row) ? BREAK_H : 0);

  const templateRows = breakAfter === null
    ? `${HEAD_H}px repeat(${slots.length}, ${rowH}px)`
    : `${HEAD_H}px repeat(${breakAfter + 1}, ${rowH}px) ${BREAK_H}px `
      + `repeat(${slots.length - breakAfter - 1}, ${rowH}px)`;

  // Where "now" falls among the visible rows: the first row that has not
  // started yet. Off-day and off-board values simply do not draw.
  const nowRow = useMemo(() => {
    if (lockedBefore === null) return null;
    if (clock.dayOf(lockedBefore) !== day) return null;
    const s = clock.slotOf(lockedBefore);
    let row = 0;
    for (let i = 0; i < slots.length; i++) if (slots[i] <= s) row = i + 1;
    return row > 0 && row < slots.length ? row : null;
  }, [lockedBefore, day, clock, slots]);

  const onThisDay = appointments.filter((a) => a.day === day);
  const todays = onThisDay.filter((a) => a.room);
  // An interview with no room cannot be drawn on a room x time grid, but
  // dropping it silently would hide a real hard-constraint failure.
  const roomless = onThisDay.length - todays.length;

  const gridW = gutter + rooms.length * colW;

  return (
    <div className="grid-scroll">
      {roomless > 0 && (
        <div className="callout err" style={{ margin: "0 0 10px" }}>
          {roomless} interview{roomless === 1 ? "" : "s"} on this day could not
          be assigned a room, so {roomless === 1 ? "it is" : "they are"} not on
          the board. This is a hard-constraint failure, not a display limit.
        </div>
      )}
      <div
        className="grid"
        style={{
          gridTemplateColumns: `${gutter}px repeat(${rooms.length}, ${colW}px)`,
          gridTemplateRows: templateRows,
        }}
      >
        <div className="grid-corner" />
        {rooms.map((r) => {
          const util = roomUtilisation?.[r.id];
          return (
            <div
              key={r.id}
              className={`grid-roomhead${roomBlockedToday.has(r.id) ? " blocked" : ""}`}
              title={util === undefined
                ? blockedReason(r, day)
                : `${blockedReason(r, day)} — ${util}% used across the week`}
            >
              {r.name.replace(/^Room /, "R")}
              {util !== undefined && (
                <span className="util" aria-hidden>
                  <span
                    className={util >= 92 ? "hot" : undefined}
                    style={{ width: `${Math.min(100, Math.max(3, util))}%` }}
                  />
                </span>
              )}
            </div>
          );
        })}

        {slots.map((s, row) => (
          <TimeRow
            key={s}
            slot={s}
            gridRow={gridRowOf(row)}
            rooms={rooms}
            clock={clock}
            blockedCells={blockedCells}
          />
        ))}

        {breakAfter !== null && (
          <div
            className="grid-break"
            style={{ gridRow: breakAfter + 3, gridColumn: `1 / -1` }}
          >
            <span>Lunch · {cfg.config.lunch[0]}–{cfg.config.lunch[1]}</span>
          </div>
        )}

        {nowRow !== null && (
          <div
            className="grid-now"
            style={{ top: yOf(nowRow) - 1, left: gutter, width: gridW - gutter }}
            title="Interviews above this line have already happened and will not be moved"
          />
        )}

        {todays.map((a) => {
          const row = rowOf.get(a.slot);
          const col = roomCol.get(a.room!);
          if (row === undefined || col === undefined) return null;

          // Duration can run past lunch in row terms; clamp to the visible run.
          const endRow = rowOf.get(a.slot + a.duration_slots - 1) ?? row;
          const state = changeState(a.id);
          const locked =
            lockedBefore !== null && clock.abs(a.day, a.slot) < lockedBefore;
          const focused = focusStudent !== null && a.student_id === focusStudent;
          const dim =
            (focusStudent !== null && !focused) ||
            (companyFilter !== null && a.company_id !== companyFilter);

          const top = yOf(row);
          // Measured to the END of the last row rather than multiplied out, so
          // an interview that straddles lunch grows over the break instead of
          // ending an hour early.
          const height = Math.max(rowH, yOf(endRow) + rowH - top) - 3;

          return (
            <button
              key={a.id}
              className={[
                "appt",
                `tier-${a.tier}`,
                state ? `is-${state}` : "",
                locked ? "is-locked" : "",
                dim ? "dimmed" : "",
                focused ? "focused" : "",
              ].filter(Boolean).join(" ")}
              style={{
                top: top + 1,
                left: gutter + col * colW + 1,
                width: colW - 3,
                height,
              }}
              onClick={() => onPick(a)}
              title={[
                companyName(a.company_id),
                `Student ${a.student_id}`,
                `${clock.label(a.slot)}–${clock.label(a.slot + a.duration_slots)}`
                  + ` · ${clock.minutes(a.duration_slots)} min`,
                `${a.room} · panel ${a.panel}`,
                locked ? "Already under way — locked" : "",
                state === "moved" ? "Proposal: moves to this slot" : "",
                state === "added" ? "Proposal: newly placed here" : "",
                state === "cut" ? "Proposal: cancelled" : "",
              ].filter(Boolean).join("\n")}
            >
              {state && <span className="mark">{MARK[state]}</span>}
              <span className="co">{shortName(companyName(a.company_id))}</span>
              {/* Two text lines need 32px: 4px padding plus a 15px and a 13px
                  line box. Below that only the company name fits. */}
              {height >= 32 && <span className="st">{a.student_id}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TimeRow({
  slot, gridRow, rooms, clock, blockedCells,
}: {
  slot: number; gridRow: number; rooms: Room[]; clock: Clock;
  blockedCells: Set<string>;
}) {
  const hour = clock.isHour(slot);
  return (
    <>
      <div
        className={`grid-time${hour ? " hour" : ""}`}
        style={{ gridRow, gridColumn: 1 }}
      >
        {hour ? clock.label(slot) : ""}
      </div>
      {rooms.map((r, i) => (
        <div
          key={r.id}
          className={[
            "grid-cell",
            i % 2 === 1 ? "odd" : "",
            hour ? "hour" : "",
            blockedCells.has(`${r.id}:${slot}`) ? "blocked" : "",
          ].filter(Boolean).join(" ")}
          style={{ gridRow, gridColumn: i + 2 }}
        />
      ))}
    </>
  );
}

function blockedReason(room: Room, day: number) {
  const w = room.blocked_windows.find((x) => x.day === day);
  return w ? `${room.name} — ${w.reason}` : room.name;
}

/**
 * A cell fits roughly twelve characters, so the few long names get a known
 * abbreviation rather than a mid-word ellipsis. Anything unlisted falls through
 * to CSS truncation; the full name is always in the block's tooltip.
 */
const ABBREV: Record<string, string> = {
  "Tech Mahindra": "TechM",
  "Tower Research": "Tower",
  "Morgan Stanley": "MorganS",
  "Goldman Sachs": "Goldman",
  "Jane Street": "JaneSt",
  "D. E. Shaw": "DEShaw",
};

function shortName(name: string) {
  return ABBREV[name] ?? name;
}
