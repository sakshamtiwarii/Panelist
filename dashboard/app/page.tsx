"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError, api,
  type Appointment, type ConfigResponse, type Diagnostics,
  type DisruptionEvent, type Metrics, type Proposal, type Session,
  type SolverReport,
} from "@/lib/api";
import { makeClock } from "@/lib/time";
import DiffPanel from "@/components/DiffPanel";
import LoginScreen from "@/components/LoginScreen";
import DisruptionPanel from "@/components/DisruptionPanel";
import MetricsBand from "@/components/MetricsBand";
import ScheduleGrid, { type ChangeState } from "@/components/ScheduleGrid";

/**
 * Coordinator console.
 *
 * The whole surface is one screen on purpose: during a live disruption the
 * board, the events being injected and the proposed fix all need to be
 * visible at once. Routing between them would mean losing the schedule from
 * view exactly when it matters most.
 *
 * A proposal is *previewed on the board* before it is applied — moved and
 * cancelled interviews are recoloured in place, so the coordinator sees the
 * change against the real schedule rather than reading a list and imagining
 * where it lands.
 */

export default function Page() {
  const [session, setSession] = useState<Session | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [cfg, setCfg] = useState<ConfigResponse | null>(null);
  const [appts, setAppts] = useState<Appointment[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [solver, setSolver] = useState<SolverReport | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);

  const [day, setDay] = useState(0);
  const [queue, setQueue] = useState<DisruptionEvent[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nowSlot, setNowSlot] = useState<number | null>(null);
  const [churnCap, setChurnCap] = useState(10);
  const [focusStudent, setFocusStudent] = useState<string | null>(null);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");

  // Explicit choice stamps the root; "system" removes the stamp and lets
  // prefers-color-scheme decide.
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("panelist-theme", theme); } catch { /* private mode */ }
  }, [theme]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("panelist-theme");
      if (saved === "light" || saved === "dark" || saved === "system") setTheme(saved);
    } catch { /* private mode */ }
  }, []);

  const clock = useMemo(() => (cfg ? makeClock(cfg) : null), [cfg]);

  const companyName = useCallback(
    (id: string) => cfg?.companies.find((c) => c.id === id)?.name ?? id,
    [cfg],
  );

  /* ---- loading ---------------------------------------------------------- */

  const refresh = useCallback(async () => {
    const [s, m] = await Promise.all([api.schedule(), api.metrics()]);
    setAppts(s.appointments);
    setMetrics(m);
    try {
      setDiag(await api.diagnostics());
    } catch {
      setDiag(null);
    }
  }, []);

  // Resume an existing session before deciding whether to show the login gate,
  // so a page refresh does not sign the coordinator out mid-disruption.
  useEffect(() => {
    (async () => {
      try {
        setSession(await api.me());
      } catch {
        setSession(null);
      } finally {
        setAuthChecked(true);
      }
    })();
  }, []);

  useEffect(() => {
    if (!session) return;
    (async () => {
      try {
        setCfg(await api.config());
        const h = await api.health();
        if (h.has_schedule) await refresh();
      } catch (e) {
        setError(describeError(e));
      }
    })();
  }, [session, refresh]);

  const signOut = async () => {
    try { await api.logout(); } catch { /* cookie may already be gone */ }
    setSession(null);
    setCfg(null);
    setAppts([]);
    setMetrics(null);
    setProposal(null);
    setQueue([]);
  };

  const solve = async () => {
    setBusy("Solving");
    setError(null);
    try {
      const r = await api.solve("primary", 30);
      setSolver(r.solver);
      setProposal(null);
      setQueue([]);
      await refresh();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  /* ---- replan ----------------------------------------------------------- */

  const propose = async () => {
    if (!queue.length) return;
    setBusy("Replanning");
    setError(null);
    try {
      setProposal(await api.propose({
        disruptions: queue,
        churn_cap_pct: churnCap,
        time_limit_seconds: 60,
        now_slot: nowSlot,
      }));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const applyProposal = async () => {
    if (!proposal?.proposal_id) return;
    setBusy("Applying");
    try {
      await api.apply(proposal.proposal_id);
      setProposal(null);
      setQueue([]);
      await refresh();
    } catch (e) {
      // Roster edits are validated server-side (CGPA cutoffs, duplicates,
      // unknown ids) and come back as 400s with a readable reason.
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  /* ---- proposal preview on the board ------------------------------------ */

  const changeState = useCallback(
    (id: string): ChangeState => {
      const d = proposal?.diff;
      if (!d) return null;
      if (d.moved.includes(id)) return "moved";
      if (d.added.includes(id)) return "added";
      if (d.removed.includes(id)) return "cut";
      return null;
    },
    [proposal],
  );

  // While a proposal is open the board shows the PROPOSED placement, with
  // interviews the proposal would cancel kept visible in their old slot so
  // the loss is legible rather than a silent absence.
  const boardAppts = useMemo(() => {
    const d = proposal?.diff;
    if (!d || !proposal?.ok) return appts;
    const cancelled = new Set([...d.removed]);
    const moves = new Map(d.moved_detail.map((m) => [m.id, m.to!]));
    return appts.map((a) => {
      if (cancelled.has(a.id)) return a;
      const to = moves.get(a.id);
      return to ? { ...a, day: to.day, slot: to.slot, room: to.room, panel: to.panel } : a;
    });
  }, [appts, proposal]);

  const perDayCount = useMemo(() => {
    const counts = new Map<number, number>();
    boardAppts.forEach((a) => counts.set(a.day, (counts.get(a.day) ?? 0) + 1));
    return counts;
  }, [boardAppts]);

  const describeEvent = useCallback(
    (e: DisruptionEvent) => {
      if (!clock) return e.type;
      switch (e.type) {
        case "company_late":
          return `${companyName(e.company_id!)} — ${e.hours}h late, Day ${(e.day ?? 0) + 1}`;
        case "panel_drop":
          return `${companyName(e.company_id!)} — panel out from ${clock.stamp(e.from_slot!)}`;
        case "student_withdraw":
          return `${e.student_id} — withdraws from ${clock.stamp(e.from_slot!)}`;
        case "room_unavailable":
          return `${e.room_id} — unavailable Day ${(e.day ?? 0) + 1}`;
        case "company_add":
          return `Add ${e.name} — ${e.shortlist_size} students at CGPA ${e.cgpa_cutoff}+`;
        case "company_remove":
          return `${companyName(e.company_id!)} — withdraws from the week`;
        case "shortlist_add":
          return `${e.student_id} → ${companyName(e.company_id!)} shortlist`;
        case "shortlist_remove":
          return `${e.student_id} off ${companyName(e.company_id!)} shortlist`;
        default:
          return e.type;
      }
    },
    [clock, companyName],
  );

  /* ---- render ----------------------------------------------------------- */

  if (!authChecked) {
    return <div className="bare"><span className="hint">Checking session…</span></div>;
  }

  if (!session) {
    return <LoginScreen onSignedIn={setSession} />;
  }

  if (error && !cfg) {
    return (
      <div className="bare">
        <div className="bare-card">
          <h2>Can&rsquo;t reach the scheduler</h2>
          <p className="hint">
            The console is running, but the API behind it isn&rsquo;t
            answering. Start it and this page will connect on reload.
          </p>
          <ol className="steps">
            <li>
              Run <code>docker compose up</code> from the project root — or{" "}
              <code>uvicorn main:app --port 8000</code> from <code>api/</code>.
            </li>
            <li>Reload this page.</li>
          </ol>
          <p className="hint" style={{ marginTop: 4, color: "var(--ink-3)" }}>
            {error}
          </p>
        </div>
      </div>
    );
  }

  if (!cfg || !clock) {
    return <div className="bare"><span className="hint">Loading console…</span></div>;
  }

  const hasSchedule = appts.length > 0;

  return (
    <div className="shell">
      <header className="topbar">
        <span className="wordmark">
          Panelist<span>coordinator console</span>
        </span>

        <span className="pill">
          <span
            className={`dot ${
              !hasSchedule ? "idle" : solver && !solver.optimal ? "warn" : ""
            }`}
          />
          {!hasSchedule
            ? "No schedule yet"
            : solver && !solver.optimal
              ? `Best found in ${solver.wall_time_seconds}s`
              : "Schedule optimal"}
        </span>

        <span className="spacer" />

        <label className="ctl" title="How much of the schedule a fix may move before it needs your sign-off">
          Move limit
          <input
            className="input num"
            style={{ width: 58 }}
            type="number"
            min={1}
            max={100}
            value={churnCap}
            onChange={(e) => setChurnCap(Number(e.target.value))}
          />
          %
        </label>

        <label className="ctl" title="Interviews before this time have already happened and will not be moved">
          Time now
          <select
            className="select"
            style={{ width: 128 }}
            value={nowSlot ?? ""}
            onChange={(e) =>
              setNowSlot(e.target.value === "" ? null : Number(e.target.value))
            }
          >
            <option value="">Week not started</option>
            {Array.from({ length: cfg.config.days }, (_, d) =>
              cfg.config.usable_slots_per_day
                .filter((s) => clock.isHour(s))
                .map((s) => (
                  <option key={`${d}:${s}`} value={clock.abs(d, s)}>
                    Day {d + 1}, {clock.label(s)}
                  </option>
                )),
            )}
          </select>
        </label>

        <span className="pill" title={`Signed in as ${session.username}`}>
          {session.display_name}
          {session.role === "viewer" && (
            <span className="urg normal" style={{ marginLeft: 2 }}>READ ONLY</span>
          )}
        </span>

        <button className="btn" onClick={signOut}>Sign out</button>

        <div className="seg" role="group" aria-label="Theme">
          {(["light", "system", "dark"] as const).map((m) => (
            <button
              key={m}
              aria-pressed={theme === m}
              onClick={() => setTheme(m)}
              title={`${m[0].toUpperCase() + m.slice(1)} theme`}
            >
              {m === "light" ? "Light" : m === "dark" ? "Dark" : "Auto"}
            </button>
          ))}
        </div>

        <button
          className="btn btn-primary"
          onClick={solve}
          disabled={!!busy || session.role !== "coordinator"}
          title={session.role !== "coordinator"
            ? "Building the schedule needs a coordinator account"
            : undefined}
        >
          {busy === "Solving"
            ? "Solving…"
            : hasSchedule ? "Rebuild schedule" : "Build schedule"}
        </button>
      </header>

      {metrics ? (
        <MetricsBand m={metrics} />
      ) : (
        <div className="metrics">
          <div className="metric" style={{ minWidth: 320 }}>
            <span className="label">Getting started</span>
            <div className="metric-value" style={{ fontSize: 14, fontFamily: "var(--ui)" }}>
              No schedule built yet
            </div>
            <div className="metric-sub">
              Press <strong>Build schedule</strong> to solve the placement week.
            </div>
          </div>
        </div>
      )}

      <div className={`body${proposal ? " with-diff" : ""}`}>
        <div className="rail">
          <DisruptionPanel
            companies={cfg.companies}
            rooms={cfg.rooms}
            days={cfg.config.days}
            slots={cfg.config.usable_slots_per_day}
            clock={clock}
            queue={queue}
            onQueue={(e) => setQueue((q) => [...q, e])}
            onDrop={(i) => setQueue((q) => q.filter((_, j) => j !== i))}
            describe={describeEvent}
          />

          <div className="rail-section">
            <span className="label" style={{ display: "block", marginBottom: 10 }}>
              3 · Find a fix
            </span>
            <button
              className="btn btn-primary btn-lg"
              style={{ width: "100%" }}
              onClick={propose}
              disabled={!queue.length || !!busy || !hasSchedule}
            >
              {busy === "Replanning"
                ? "Working out a fix…"
                : queue.length
                  ? `Replan around ${queue.length} change${queue.length === 1 ? "" : "s"}`
                  : "Replan"}
            </button>
            <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
              You&rsquo;ll get a proposal to review first. Nothing on the
              schedule changes until you approve it.
            </p>
          </div>

          {diag && diag.unscheduled > 0 && (
            <div className="rail-section">
              <span className="label">Can&rsquo;t be placed ({diag.unscheduled})</span>
              {diag.capacity?.structural_shortfall && (
                <p className="hint" style={{ marginBottom: 10 }}>
                  There is {diag.capacity.load_ratio}× more demand than there
                  are rooms and hours. No schedule can fit them all — these are
                  the companies worst affected.
                </p>
              )}
              {diag.per_company.slice(0, 6).map((c) => (
                <div className="risk-row" key={c.company_id} title={c.reason}>
                  <span>{c.company}</span>
                  <span className="n num">
                    {c.unscheduled}/{c.demand}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="rail-section" style={{ borderBottom: "none" }}>
            <span className="label">Reading the board</span>
            <div className="legend" style={{ marginTop: 8, marginBottom: 12 }}>
              <span className="legend-item"><span className="swatch t1" /> Mass recruiter</span>
              <span className="legend-item"><span className="swatch t2" /> Mid-size</span>
              <span className="legend-item"><span className="swatch t3" /> Niche</span>
            </div>
            <span className="label">During a replan</span>
            <div className="legend" style={{ marginTop: 8 }}>
              <span className="legend-item"><span className="swatch moved" /> ⇅ moved</span>
              <span className="legend-item"><span className="swatch cut" /> × cancelled</span>
              <span className="legend-item"><span className="swatch added" /> + added</span>
            </div>
            <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
              Click any interview to trace that student across the week.
            </p>
          </div>
        </div>

        <main className="main">
          <div className="tabs">
            {Array.from({ length: cfg.config.days }, (_, d) => (
              <button
                key={d}
                className="tab"
                role="tab"
                aria-selected={day === d}
                onClick={() => setDay(d)}
              >
                Day {d + 1}
                <span className="count">{perDayCount.get(d) ?? 0}</span>
              </button>
            ))}
            <span className="spacer" />
            {error && <span className="banner err">{error}</span>}
            {proposal?.ok && (
              <span className="banner">
                Previewing a proposed fix — nothing saved yet
              </span>
            )}
          </div>

          {hasSchedule ? (
            <ScheduleGrid
              day={day}
              cfg={cfg}
              clock={clock}
              appointments={boardAppts}
              companyName={companyName}
              changeState={changeState}
              lockedBefore={nowSlot}
              focusStudent={focusStudent}
              onPick={(a) =>
                setFocusStudent((s) => (s === a.student_id ? null : a.student_id))
              }
            />
          ) : (
            <div className="bare">
              <div className="bare-card">
                <h2>Build the week&rsquo;s schedule</h2>
                <p className="hint">
                  Nothing is scheduled yet. Solving places every shortlisted
                  interview into a room and time slot without clashing a
                  student, a room, or an interview panel.
                </p>
                <ol className="steps">
                  <li>
                    Press <strong>Build schedule</strong> above — it takes a
                    second or two.
                  </li>
                  <li>
                    Read the board: rooms across the top, time down the side.
                  </li>
                  <li>
                    When something goes wrong, add it on the left and replan.
                  </li>
                </ol>
              </div>
            </div>
          )}
        </main>

        {proposal && clock && (
          <DiffPanel
            proposal={proposal}
            canApply={session.role === "coordinator"}
            clock={clock}
            companyName={companyName}
            applying={busy === "Applying"}
            onApply={applyProposal}
            onReject={() => setProposal(null)}
            onHoverStudent={setFocusStudent}
          />
        )}
      </div>
    </div>
  );
}

function describeError(e: unknown) {
  if (e instanceof ApiError) {
    const d = e.detail as { detail?: string } | string;
    if (typeof d === "string") return d;
    return d?.detail ?? `API error ${e.status}`;
  }
  return e instanceof Error ? e.message : String(e);
}
