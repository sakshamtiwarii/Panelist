"use client";

import type { Metrics } from "@/lib/api";
import Icon, { type IconName } from "./Icon";

/**
 * The always-visible metrics row. Each tile is label / value / meter / sub.
 *
 * The meter carries severity in its fill, over a track that is a light step of
 * the same hue, so the state reads across the whole bar rather than only the
 * filled part. Tiles that change tone also change glyph — "0" and "3" in green
 * and red are the same shape to a reader who cannot separate the hues.
 */

type Tone = "good" | "warn" | "bad" | "neutral";

interface Tile {
  label: string;
  value: string;
  unit?: string;
  sub: string;
  tone: Tone;
  /** 0–100. Omitted where the number has no ceiling to be read against. */
  fill?: number;
  icon?: IconName;
  title: string;
}

export default function MetricsBand({ m }: { m: Metrics }) {
  // Wait time has no natural maximum, so it is scaled against a half-day.
  const waitFill = Math.min(100, (m.avg_student_wait_minutes / 240) * 100);

  const tiles: Tile[] = [
    {
      label: "Scheduled",
      value: m.pct_scheduled.toFixed(1),
      unit: "%",
      sub: `${m.interviews_scheduled} of ${m.interviews_total} interviews`,
      tone: m.pct_scheduled === 100 ? "good" : m.pct_scheduled >= 95 ? "warn" : "bad",
      fill: m.pct_scheduled,
      title: "Share of shortlisted interviews the solver placed into a room and a slot.",
    },
    {
      label: "Clashes",
      value: String(m.student_clashes),
      sub: m.student_clashes === 0 ? "None — guaranteed" : "Invariant broken",
      tone: m.student_clashes === 0 ? "good" : "bad",
      icon: m.student_clashes === 0 ? "check" : "warning",
      title: "Students double-booked at the same time. The solver guarantees zero; anything else is a bug, not a trade-off.",
    },
    {
      label: "Room use",
      value: m.room_utilization_pct.toFixed(1),
      unit: "%",
      sub: "of available room-slots",
      tone: m.room_utilization_pct > 92 ? "warn" : "neutral",
      fill: m.room_utilization_pct,
      title: "How full the rooms are across the week. Above ~92% there is little slack left to absorb a disruption.",
    },
    {
      label: "Avg wait",
      value: String(Math.round(m.avg_student_wait_minutes)),
      unit: "min",
      sub: `Longest ${m.max_student_wait_minutes} min`,
      tone: m.avg_student_wait_minutes > 150 ? "warn" : "neutral",
      fill: waitFill,
      title: "Average gap a student spends on campus between their first and last interview of a day.",
    },
    {
      label: "Students",
      value: String(m.students_with_interviews),
      sub: `${m.avg_interviews_per_student} interviews each`,
      tone: "neutral",
      title: "Students with at least one interview scheduled this week.",
    },
    {
      label: "Unplaced",
      value: String(m.interviews_unscheduled),
      sub: m.interviews_unscheduled ? "See diagnostics, left" : "Nothing left over",
      tone: m.interviews_unscheduled ? "bad" : "good",
      icon: m.interviews_unscheduled ? "warning" : "check",
      title: "Interviews with nowhere to go. Usually means demand exceeds rooms x hours.",
    },
  ];

  return (
    <div className="metrics">
      {tiles.map((t) => (
        <div className="metric" key={t.label} title={t.title}>
          <div className="metric-head">
            <span className="label">{t.label}</span>
            {t.icon && (
              <Icon
                name={t.icon}
                size={13}
                style={{
                  marginLeft: "auto",
                  color: t.tone === "bad" ? "var(--st-cut)"
                    : t.tone === "good" ? "var(--st-added)"
                      : "var(--ink-3)",
                }}
              />
            )}
          </div>

          <div
            className={`metric-value${
              t.tone === "good" ? " is-good" : t.tone === "bad" ? " is-bad" : ""
            }`}
          >
            {t.value}
            {t.unit && <span className="unit">{t.unit}</span>}
          </div>

          {t.fill !== undefined ? (
            <div className={`meter ${t.tone}`}>
              <span style={{ width: `${Math.max(2, Math.min(100, t.fill))}%` }} />
            </div>
          ) : (
            // Keeps every tile the same height so the values share a baseline.
            <div style={{ height: 4, marginTop: 8 }} />
          )}

          <div className="metric-sub">{t.sub}</div>
        </div>
      ))}
    </div>
  );
}

/** Placeholder tiles while a solve runs, so the band keeps its height. */
export function MetricsSkeleton() {
  return (
    <div className="metrics" aria-hidden>
      {Array.from({ length: 6 }, (_, i) => (
        <div className="metric" key={i}>
          <div className="skel" style={{ width: 62, height: 9 }} />
          <div className="skel" style={{ width: 74, height: 22, marginTop: 8 }} />
          <div className="skel" style={{ width: "100%", height: 4, marginTop: 9 }} />
          <div className="skel" style={{ width: 88, height: 8, marginTop: 8 }} />
        </div>
      ))}
    </div>
  );
}
