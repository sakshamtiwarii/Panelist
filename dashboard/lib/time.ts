import type { ConfigResponse } from "./api";

/**
 * Slot arithmetic mirrors the solver's, but the constants come from /config
 * rather than being redeclared here — a frontend copy that drifts from the
 * backend renders a board that is wrong without ever looking wrong.
 */
export function makeClock(cfg: ConfigResponse) {
  const { slots_per_day_raw: raw, day_start_minutes: dayStart } = cfg;
  const step = cfg.config.slot_minutes;

  const label = (slotInDay: number) => {
    const m = dayStart + slotInDay * step;
    return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  };

  return {
    label,
    /** Slots in a day INCLUDING the lunch band — the raw grid width. */
    raw,
    /** Absolute slot -> "D2 14:15" */
    stamp: (abs: number) => `D${Math.floor(abs / raw) + 1} ${label(abs % raw)}`,
    /** Day + slot-in-day -> absolute slot */
    abs: (day: number, slot: number) => day * raw + slot,
    dayOf: (abs: number) => Math.floor(abs / raw),
    slotOf: (abs: number) => abs % raw,
    isHour: (slotInDay: number) => (dayStart + slotInDay * step) % 60 === 0,
    minutes: (slots: number) => slots * step,
  };
}

export type Clock = ReturnType<typeof makeClock>;
