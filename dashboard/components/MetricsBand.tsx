"use client";

import type { Metrics } from "@/lib/api";

/**
 * Metrics stay visible at all times rather than living in a report (guide
 * section 3). Rendered as a rule-separated band, not cards: a coordinator
 * scans these, and cards impose boxes the eye has to cross.
 *
 * Clash count is styled by value — zero is the guarantee the whole model
 * rests on, so a non-zero reads as an alarm rather than a statistic.
 */

export default function MetricsBand({ m }: { m: Metrics }) {
  const items = [
    {
      label: "Scheduled",
      value: m.pct_scheduled.toFixed(1),
      unit: "%",
      sub: `${m.interviews_scheduled} / ${m.interviews_total}`,
      tone: m.pct_scheduled === 100 ? "is-good" : "",
    },
    {
      label: "Clashes",
      value: String(m.student_clashes),
      unit: "",
      sub: m.student_clashes === 0 ? "guaranteed" : "INVARIANT BROKEN",
      tone: m.student_clashes === 0 ? "is-good" : "is-bad",
    },
    {
      label: "Room use",
      value: m.room_utilization_pct.toFixed(1),
      unit: "%",
      sub: "of available slots",
      tone: "",
    },
    {
      label: "Avg wait",
      value: String(Math.round(m.avg_student_wait_minutes)),
      unit: "min",
      sub: `max ${m.max_student_wait_minutes}`,
      tone: "",
    },
    {
      label: "Students",
      value: String(m.students_with_interviews),
      unit: "",
      sub: `${m.avg_interviews_per_student} each`,
      tone: "",
    },
    {
      label: "Unplaced",
      value: String(m.interviews_unscheduled),
      unit: "",
      sub: m.interviews_unscheduled ? "see diagnostics" : "none",
      tone: m.interviews_unscheduled ? "is-bad" : "is-good",
    },
  ];

  return (
    <div className="metrics">
      {items.map((it) => (
        <div className="metric" key={it.label}>
          <span className="label">{it.label}</span>
          <div className={`metric-value ${it.tone}`}>
            {it.value}
            {it.unit && <span className="unit">{it.unit}</span>}
          </div>
          <div className="metric-sub">{it.sub}</div>
        </div>
      ))}
    </div>
  );
}
