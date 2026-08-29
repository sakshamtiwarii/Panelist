"use client";

import { useState } from "react";
import type { ChangeDetail, Proposal } from "@/lib/api";
import type { Clock } from "@/lib/time";
import Icon from "./Icon";

/**
 * The proposal review surface — the only place the schedule can change.
 *
 * Churn is shown against the cap as a headline number and a bar before any list
 * of changes, since "is this fix proportionate" is the question being asked.
 * Forced and elective churn stay visually separate: the coordinator did not
 * choose the cancellations a withdrawal caused, and one blended figure makes a
 * modest fix look reckless.
 */

interface Props {
  proposal: Proposal;
  canApply: boolean;
  clock: Clock;
  companyName: (id: string) => string;
  applying: boolean;
  onApply: (useAlternative?: boolean) => void;
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
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="alert" size={15} style={{ color: "var(--st-cut)" }} />
            <span className="label" style={{ color: "var(--st-cut)" }}>Replan failed</span>
          </div>
        </div>
        <div style={{ padding: 14 }}>
          <div className="callout err">{proposal.reason}</div>
          {proposal.lock_conflicts?.length ? (
            <ul style={{ margin: "12px 0 0", paddingLeft: 16, fontSize: 12 }}>
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
  // Present only when re-solving found a meaningfully cheaper fix; otherwise
  // the replanner reports the churn as irreducible.
  const alt = proposal.alternative ?? null;
  const unplaced = proposal.unscheduled ?? 0;
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
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="swatch moved" />
          <span className="label">{proposal.label ?? "Proposed fix"}</span>
          <span className="spacer" />
          <span className="num" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            solved in {proposal.solver?.wall_time_seconds}s
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

        {/* Proportional figures, not tabular: at 40px tabular digits read
            visibly loose. */}
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span className={`hero-n${over ? " over" : ""}`}>
            {d.elective_churn_count}
          </span>
          <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
            of {d.baseline_appointments}<br />appointments moved
          </span>
        </div>

        <div className={`churn-bar${over ? " over" : ""}`}>
          <div
            className={`churn-fill${over ? " over" : ""}`}
            style={{ width: `${Math.min(100, (pct / scaleMax) * 100)}%` }}
          />
          <div
            className="churn-cap"
            style={{ left: `${(cap / scaleMax) * 100}%` }}
            title={`Your limit: ${cap}%`}
          />
        </div>
        <div style={{ display: "flex", fontSize: 11, color: "var(--ink-3)" }}>
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

        {alt && (
          <div className="callout" style={{ marginTop: 10 }}>
            <strong>A lower-churn fix exists.</strong>
            <div className="alt-compare">
              <div>
                <span className="label">This fix</span>
                <div className="alt-n">{d.elective_churn_count} moved</div>
                <div className="alt-sub">{unplaced} left unplaced</div>
              </div>
              <div>
                <span className="label">Alternative</span>
                <div className="alt-n">{alt.elective_churn_count} moved</div>
                <div className="alt-sub">{alt.unscheduled} left unplaced</div>
              </div>
            </div>
            <p className="hint" style={{ margin: "8px 0 0" }}>
              {alt.unscheduled > unplaced ? (
                <>
                  Moving {d.elective_churn_count - alt.elective_churn_count}{" "}
                  fewer interviews costs {alt.unscheduled - unplaced} more left
                  unplaced. Which matters more is your call, not the
                  solver&rsquo;s.
                </>
              ) : (
                "It moves fewer interviews and leaves no more unplaced."
              )}
            </p>
          </div>
        )}
        {!canApply && (
          <div className="callout info">
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
              aria-expanded={!!open[g.key]}
              onClick={() => setOpen((o) => ({ ...o, [g.key]: !o[g.key] }))}
            >
              <span className={`swatch ${g.swatch}`} />
              <span style={{ fontSize: 13, fontWeight: 560 }}>{g.label}</span>
              <span className="n">{g.items.length}</span>
              <Icon name="chevron" size={13} className="chev" />
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

        {proposal.notify && proposal.notify.total_people_to_contact > 0 && (
          <section className="diff-group">
            <button
              className="diff-group-head"
              aria-expanded={!!open.notify}
              onClick={() => setOpen((o) => ({ ...o, notify: !o.notify }))}
            >
              <Icon name="alert" size={13} style={{ color: "var(--ink-3)" }} />
              <span style={{ fontSize: 13, fontWeight: 560 }}>
                Who needs telling
              </span>
              <span className="n">
                {proposal.notify.total_people_to_contact}
              </span>
              <Icon name="chevron" size={13} className="chev" />
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
          onClick={() => onApply(false)}
          disabled={applying || !canApply
                    || !!proposal.verification_errors?.length}
          title={!canApply
            ? "Applying a fix changes the live schedule and needs a coordinator account"
            : undefined}
        >
          {applying
            ? <><span className="spinner" />Applying…</>
            : over ? "Apply anyway" : "Apply this fix"}
        </button>
        {alt && (
          <button
            className="btn"
            onClick={() => onApply(true)}
            disabled={applying || !canApply
                      || !!alt.verification_errors?.length}
            title="Commit the lower-churn schedule instead"
          >
            Apply the {alt.elective_churn_count}-move fix
          </button>
        )}
        <button className="btn btn-danger" onClick={onReject} disabled={applying}>
          Discard
        </button>
      </div>
    </aside>
  );
}
