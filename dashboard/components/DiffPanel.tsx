"use client";

import { useState } from "react";
import type { ChangeDetail, Proposal } from "@/lib/api";
import type { Clock } from "@/lib/time";

/**
 * The proposal review surface — the only place the schedule can change.
 *
 * Two things drive the layout. First, churn is shown against the cap as a bar
 * before any list of changes, because "is this fix proportionate" is the
 * question being asked and a number in a paragraph doesn't answer it fast
 * enough. Second, forced and elective churn are visually separated: the
 * coordinator did not choose the cancellations a withdrawal caused, and
 * blending them into one figure makes a modest fix look reckless.
 */

interface Props {
  proposal: Proposal;
  canApply: boolean;
  clock: Clock;
  companyName: (id: string) => string;
  applying: boolean;
  onApply: () => void;
  onReject: () => void;
  onHoverStudent: (sid: string | null) => void;
}

export default function DiffPanel({
  proposal, canApply, clock, companyName, applying, onApply, onReject,
  onHoverStudent,
}: Props) {
  const [open, setOpen] = useState<Record<string, boolean>>({ moved: true });
  const d = proposal.diff;

  if (!proposal.ok || !d) {
    return (
      <aside className="diff">
        <div className="diff-head">
          <span className="label">Replan failed</span>
        </div>
        <div style={{ padding: 14 }}>
          <div className="callout err">{proposal.reason}</div>
          {proposal.lock_conflicts?.length ? (
            <ul style={{ margin: "12px 0 0", paddingLeft: 16, fontSize: 11.5 }}>
              {proposal.lock_conflicts.map((c, i) => (
                <li key={i} style={{ marginBottom: 6 }}>{c}</li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="diff-actions">
          <button className="btn" onClick={onReject}>Dismiss</button>
        </div>
      </aside>
    );
  }

  const cap = proposal.churn_cap_pct ?? 10;
  const pct = d.elective_churn_pct;
  const over = !!proposal.cap_exceeded;
  // Scale so the cap marker sits at a readable position even for tiny churn.
  const scaleMax = Math.max(cap * 1.6, pct * 1.15, 1);

  const groups: { key: string; label: string; swatch: string; items: ChangeDetail[] }[] = [
    { key: "moved", label: "Moved to a new time", swatch: "moved", items: d.moved_detail },
    { key: "added", label: "Newly placed", swatch: "added", items: d.added_detail },
    {
      key: "cut",
      label: "Cancelled to make room",
      swatch: "cut",
      items: d.removed_detail.filter((x) => d.elective_removed.includes(x.id)),
    },
    {
      key: "forced",
      label: "Cancelled by the event itself",
      swatch: "cut",
      items: d.removed_detail.filter((x) => d.forced_removed.includes(x.id)),
    },
  ];

  return (
    <aside className="diff">
      <div className="diff-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span className="label">{proposal.label ?? "Proposal"}</span>
          <span className="spacer" />
          <span className="num" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
            {proposal.solver?.wall_time_seconds}s
          </span>
        </div>

        <p className="verdict">
          {over ? (
            <>Fixing this needs <strong>{d.elective_churn_count} interviews
            moved</strong> — more than your {cap}% limit allows.</>
          ) : (
            <>This can be fixed by moving <strong>{d.elective_churn_count}{" "}
            interview{d.elective_churn_count === 1 ? "" : "s"}</strong>, well
            within your {cap}% limit.</>
          )}
        </p>

        <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 10 }}>
          <span className="num" style={{ fontSize: 26, letterSpacing: "-0.025em" }}>
            {d.elective_churn_count}
          </span>
          <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
            of {d.baseline_appointments} appointments
          </span>
        </div>

        <div className="churn-bar">
          <div
            className={`churn-fill${over ? " over" : ""}`}
            style={{ width: `${Math.min(100, (pct / scaleMax) * 100)}%` }}
          />
          <div className="churn-cap" style={{ left: `${(cap / scaleMax) * 100}%` }} />
        </div>
        <div style={{ display: "flex", fontSize: 11.5, color: "var(--ink-3)" }}>
          <span className="num">{pct}% changed</span>
          <span className="spacer" />
          <span className="num">your limit {cap}%</span>
        </div>

        {d.forced_churn_count > 0 && (
          <div className="hint" style={{ marginTop: 9 }}>
            <span className="num">{d.forced_churn_count}</span> more were
            cancelled by the disruption itself — nobody chose those, so they
            don&rsquo;t count toward your limit.
          </div>
        )}

        {proposal.authorization_prompt && (
          <div className="callout">{proposal.authorization_prompt}</div>
        )}
        {!canApply && (
          <div className="callout">
            You are signed in as a viewer. You can review this proposal in full,
            but applying it changes the live schedule and needs a coordinator
            account.
          </div>
        )}
        {proposal.verification_errors?.length ? (
          <div className="callout err">
            {proposal.verification_errors.length} hard-constraint violations —
            this proposal cannot be applied.
          </div>
        ) : null}
      </div>

      <div style={{ flex: 1 }}>
        {groups.filter((g) => g.items.length > 0).map((g) => (
          <section className="diff-group" key={g.key}>
            <button
              className="diff-group-head"
              onClick={() => setOpen((o) => ({ ...o, [g.key]: !o[g.key] }))}
            >
              <span className={`swatch ${g.swatch}`} />
              <span style={{ fontSize: 13, fontWeight: 560 }}>{g.label}</span>
              <span className="n">{g.items.length}</span>
              <span style={{ color: "var(--ink-3)", fontSize: 10 }}>
                {open[g.key] ? "▾" : "▸"}
              </span>
            </button>
            {open[g.key] &&
              g.items.slice(0, 60).map((item) => (
                <div
                  className="change"
                  key={item.id}
                  onMouseEnter={() => onHoverStudent(item.student_id)}
                  onMouseLeave={() => onHoverStudent(null)}
                >
                  <div>
                    <span className="co">{companyName(item.company_id)}</span>
                    <br />
                    <span className="who">{item.student_id}</span>
                  </div>
                  <div className="when">
                    {item.from && (
                      <span className="from">
                        {clock.stamp(clock.abs(item.from.day, item.from.slot))}
                      </span>
                    )}
                    {item.from && item.to && <br />}
                    {item.to && (
                      <span className="to">
                        {clock.stamp(clock.abs(item.to.day, item.to.slot))}
                        {item.to.room ? ` ${item.to.room}` : ""}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            {open[g.key] && g.items.length > 60 && (
              <div className="change">
                <span className="who">
                  + {g.items.length - 60} more not listed
                </span>
              </div>
            )}
          </section>
        ))}

        {proposal.notify && (
          <section className="diff-group">
            <button
              className="diff-group-head"
              onClick={() => setOpen((o) => ({ ...o, notify: !o.notify }))}
            >
              <span style={{ fontSize: 13, fontWeight: 560 }}>
                Who needs telling
              </span>
              <span className="n">
                {proposal.notify.total_people_to_contact}
              </span>
              <span style={{ color: "var(--ink-3)", fontSize: 10 }}>
                {open.notify ? "▾" : "▸"}
              </span>
            </button>
            {open.notify &&
              proposal.notify.students.slice(0, 40).map((s) => (
                <div className="notify-row" key={s.student_id}>
                  <span className={`urg${s.urgency === "high" ? "" : " normal"}`}>
                    {s.urgency === "high" ? "NOW" : "FYI"}
                  </span>
                  <span className="sid">{s.student_id}</span>
                  <span className="msg">{s.changes[0]}</span>
                </div>
              ))}
          </section>
        )}
      </div>

      <div className="diff-actions">
        <button
          className="btn btn-primary"
          onClick={onApply}
          disabled={applying || !canApply
                    || !!proposal.verification_errors?.length}
          title={!canApply
            ? "Applying a fix changes the live schedule and needs a coordinator account"
            : undefined}
        >
          {applying ? "Applying…" : over ? "Apply anyway" : "Apply this fix"}
        </button>
        <button className="btn btn-danger" onClick={onReject} disabled={applying}>
          Discard
        </button>
      </div>
    </aside>
  );
}
