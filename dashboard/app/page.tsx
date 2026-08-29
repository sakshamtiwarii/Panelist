"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError, api,
  type Appointment, type ConfigResponse, type Diagnostics,
  type DisruptionEvent, type Metrics, type PriorityLevel,
  type PriorityOverrides, type Proposal, type ReplanEvent,
  type ScheduleVersion, type Session, type SolverReport,
} from "@/lib/api";
import { makeClock } from "@/lib/time";
import DiffPanel from "@/components/DiffPanel";
import LoginScreen from "@/components/LoginScreen";
import DisruptionPanel from "@/components/DisruptionPanel";
import MetricsBand, { MetricsSkeleton } from "@/components/MetricsBand";
import ScheduleGrid, { type ChangeState } from "@/components/ScheduleGrid";
import Icon, { BrandMark } from "@/components/Icon";

/**
 * Coordinator console.
 *
 * One screen rather than several routes: during a live disruption the board,
 * the events being injected and the proposed fix all need to be visible at
 * once.
 *
 * A proposal is previewed on the board before it is applied — moved and
 * cancelled interviews are recoloured in place, so the change reads against the
 * real schedule instead of as a list to imagine.
 *
 * The top bar carries identity and the one irreversible action, the rail is a
 * numbered three-step flow (what happened → queue it → find a fix) with
 * everything else folded beneath it, and the board keeps the width.
 */

/** Geometry per density. Shared with the grid, which positions blocks in px
    and therefore cannot read the CSS variables that size the cells. */
const DENSITY = {
  comfortable: { rowH: 29, colW: 88, gutter: 66 },
  compact: { rowH: 23, colW: 72, gutter: 58 },
} as const;

type Density = keyof typeof DENSITY;

export default function Page() {
  const [session, setSession] = useState<Session | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [cfg, setCfg] = useState<ConfigResponse | null>(null);
  const [appts, setAppts] = useState<Appointment[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [solver, setSolver] = useState<SolverReport | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  // The audit trail: every schedule version and the replan that produced it.
  // Schedules are versioned rather than mutated, so this shows what has already
  // been changed today and what it cost.
  const [versions, setVersions] = useState<ScheduleVersion[]>([]);
  const [history, setHistory] = useState<ReplanEvent[]>([]);

  const [day, setDay] = useState(0);
  const [queue, setQueue] = useState<DisruptionEvent[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nowSlot, setNowSlot] = useState<number | null>(null);
  const [churnCap, setChurnCap] = useState(10);
  // Exceptions to the solver's tier priority. Held with the request rather
  // than on the company: protecting one through a particular squeeze is a
  // decision about a solve, not a standing property.
  const [priority, setPriority] = useState<PriorityOverrides>({});
  const [focusStudent, setFocusStudent] = useState<string | null>(null);
  // Day is the tab strip; these are the other two filters. Room narrows the
  // columns, company dims the rest so the surroundings stay readable.
  const [roomFilter, setRoomFilter] = useState<string | null>(null);
  const [companyFilter, setCompanyFilter] = useState<string | null>(null);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");
  const [density, setDensity] = useState<Density>("comfortable");
  const [helpOpen, setHelpOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  // The API is up and answering, but no dataset has been generated yet. A
  // fresh clone always lands here, because data/ is gitignored.
  const [needsDataset, setNeedsDataset] = useState(false);

  /*
   * Display preferences are read once on mount and only written back after that
   * read has happened. Both state values start at their SSR defaults, so an
   * ungated persist effect would fire with "system"/"comfortable" and overwrite
   * the saved choice before the load effect ran. Until `prefsLoaded`, the
   * pre-paint script in the layout owns the root attributes.
   */
  const [prefsLoaded, setPrefsLoaded] = useState(false);

  useEffect(() => {
    try {
      const t = localStorage.getItem("panelist-theme");
      if (t === "light" || t === "dark" || t === "system") setTheme(t);
      const d = localStorage.getItem("panelist-density");
      if (d === "compact" || d === "comfortable") setDensity(d);
    } catch { /* private mode: keep the defaults */ }
    setPrefsLoaded(true);
  }, []);

  // Explicit choice stamps the root; "system" removes the stamp and lets
  // prefers-color-scheme decide.
  useEffect(() => {
    if (!prefsLoaded) return;
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("panelist-theme", theme); } catch { /* private mode */ }
  }, [theme, prefsLoaded]);

  useEffect(() => {
    if (!prefsLoaded) return;
    document.documentElement.setAttribute("data-density", density);
    try { localStorage.setItem("panelist-density", density); } catch { /* private mode */ }
  }, [density, prefsLoaded]);

  const clock = useMemo(() => (cfg ? makeClock(cfg) : null), [cfg]);
  const geom = DENSITY[density];

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
    // Secondary panels: a failure here must not blank the board.
    try {
      const [v, h] = await Promise.all([api.versions(), api.history()]);
      setVersions(v.versions);
      setHistory(h.events);
    } catch {
      setVersions([]);
      setHistory([]);
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

  const load = useCallback(async () => {
    try {
      setCfg(await api.config());
      setNeedsDataset(false);
      setError(null);
      const h = await api.health();
      if (h.has_schedule) await refresh();
    } catch (e) {
      setError(describeError(e));
      // A 404 from /config means the API is healthy and there is simply no
      // dataset on disk yet, which is not the same as nothing answering.
      if (e instanceof ApiError && e.status === 404) setNeedsDataset(true);
    }
  }, [refresh]);

  useEffect(() => {
    if (!session) return;
    void load();
  }, [session, load]);

  // Escape closes whatever transient surface is open, innermost first.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (helpOpen) setHelpOpen(false);
      else if (railOpen) setRailOpen(false);
      else if (focusStudent) setFocusStudent(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [helpOpen, railOpen, focusStudent]);

  const signOut = async () => {
    try { await api.logout(); } catch { /* cookie may already be gone */ }
    setSession(null);
    setCfg(null);
    setAppts([]);
    setMetrics(null);
    setProposal(null);
    setQueue([]);
  };

  // A fresh clone has no data/ (it is gitignored). These are the settings the
  // README and CI both treat as the primary dataset.
  const generateStarter = async () => {
    setBusy("Generating");
    setError(null);
    try {
      await api.generate({
        name: "primary", seed: 42, companies: 35, students: 800,
        rooms: 20, days: 4, load_factor: 0.9,
      });
      await load();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const solve = async () => {
    setBusy("Solving");
    setError(null);
    try {
      const r = await api.solve("primary", 30, priority);
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
        priority_overrides: priority,
      }));
      setRailOpen(false);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const applyProposal = async (useAlternative = false) => {
    if (!proposal?.proposal_id) return;
    setBusy("Applying");
    try {
      await api.apply(proposal.proposal_id, useAlternative);
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

  // While a proposal is open the board shows the proposed placement, keeping
  // interviews it would cancel visible in their old slot so the loss is
  // legible rather than a silent absence.
  const boardAppts = useMemo(() => {
    const d = proposal?.diff;
    if (!d || !proposal?.ok) return appts;
    const cancelled = new Set([...d.removed]);
    const moves = new Map(d.moved_detail.map((m) => [m.id, m.to!]));
    const moved = appts.map((a) => {
      if (cancelled.has(a.id)) return a;
      const to = moves.get(a.id);
      return to ? { ...a, day: to.day, slot: to.slot, room: to.room, panel: to.panel } : a;
    });
    // Interviews the proposal adds do not exist in `appts` yet, so they are
    // synthesised here; otherwise an addition previews as nothing at all.
    const added: Appointment[] = d.added_detail
      .filter((x) => x.to)
      .map((x) => ({
        id: x.id,
        company_id: x.company_id,
        student_id: x.student_id,
        duration_slots: x.duration_slots,
        tier: x.tier,
        day: x.to!.day,
        slot: x.to!.slot,
        start: clock ? clock.abs(x.to!.day, x.to!.slot) : 0,
        end: clock ? clock.abs(x.to!.day, x.to!.slot) + x.duration_slots : 0,
        room: x.to!.room,
        panel: x.to!.panel,
      }));
    return [...moved, ...added];
  }, [appts, proposal, clock]);

  /** How many interviews the traced student has on the day being shown — with
      the rest of the board dimmed, this says whether the visible blocks are all
      of them. */
  const focusCount = useMemo(
    () => (focusStudent === null
      ? 0
      : boardAppts.filter((a) => a.day === day && a.student_id === focusStudent).length),
    [boardAppts, day, focusStudent],
  );

  const perDayCount = useMemo(() => {
    const counts = new Map<number, number>();
    boardAppts.forEach((a) => counts.set(a.day, (counts.get(a.day) ?? 0) + 1));
    return counts;
  }, [boardAppts]);

  /**
   * Who a disruption would hurt most on this day, before one happens.
   *
   * Two things make a student fragile: a full day, where any delay cascades,
   * and back-to-back interviews, with no slack to absorb an overrun. Both are
   * read off the board rather than asked of the API, so this follows a proposal
   * preview as well as the live schedule.
   */
  const atRisk = useMemo(() => {
    const byStudent = new Map<string, Appointment[]>();
    boardAppts
      .filter((a) => a.day === day)
      .forEach((a) => {
        const list = byStudent.get(a.student_id) ?? [];
        list.push(a);
        byStudent.set(a.student_id, list);
      });

    const rows = [];
    for (const [student_id, items] of byStudent) {
      items.sort((x, y) => x.start - y.start);
      let tight = 0;
      for (let i = 1; i < items.length; i++) {
        if (items[i].start === items[i - 1].end) tight++;
      }
      if (items.length >= 3 || tight > 0) {
        rows.push({ student_id, count: items.length, tight });
      }
    }
    return rows.sort((a, b) => b.tight - a.tight || b.count - a.count);
  }, [boardAppts, day]);

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
    return (
      <div className="bare">
        <div className="hint" style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <span className="spinner" /> Checking session…
        </div>
      </div>
    );
  }

  if (!session) {
    return <LoginScreen onSignedIn={setSession} />;
  }

  if (error && !cfg) {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return (
      <div className="bare">
        <div className="bare-card">
          <span className="bare-ico">
            <Icon name={needsDataset ? "layers" : "alert"} size={19} />
          </span>
          {needsDataset ? (
            <>
              <h2>No dataset yet</h2>
              <p className="hint">
                The scheduler is running and answering — there is just nothing
                for it to schedule. A fresh clone ships without one, because
                generated datasets are not kept in the repository.
              </p>
              {session.role === "coordinator" ? (
                <>
                  <p className="hint" style={{ marginTop: 10 }}>
                    Build the standard week: 35 companies, 800 students, 20
                    rooms over 4 days, at a load the solver can fully place.
                  </p>
                  <button
                    className="btn btn-primary btn-lg"
                    style={{ marginTop: 14 }}
                    onClick={generateStarter}
                    disabled={!!busy}
                  >
                    {busy === "Generating"
                      ? <><span className="spinner" />Generating…</>
                      : "Generate the starter dataset"}
                  </button>
                  <p className="hint" style={{ marginTop: 12, marginBottom: 0 }}>
                    Or run it yourself — the same settings this button uses:{" "}
                    <code>
                      python -m generator.generate --seed 42 --companies 35
                      --students 800 --rooms 20 --days 4 --load-factor 0.9
                      --out ./data/primary
                    </code>
                  </p>
                </>
              ) : (
                <p className="hint" style={{ marginTop: 10 }}>
                  Generating one changes what everybody sees, so it needs a
                  coordinator account. Ask a coordinator to press{" "}
                  <strong>Generate the starter dataset</strong>, then reload.
                </p>
              )}
            </>
          ) : (
            <>
              <h2>Can&rsquo;t reach the scheduler</h2>
              <p className="hint">
                This console is running at <code>{origin}</code>, but the
                solver behind it is not answering. Start it and this page will
                connect on reload.
              </p>
              <ol className="steps">
                <li>
                  Run <code>docker compose up</code> from the project root — or{" "}
                  <code>uvicorn api.main:app --port 8000</code> from the repo
                  root.
                </li>
                <li>Reload this page.</li>
              </ol>
            </>
          )}
          <p className="hint" style={{ marginTop: 14, marginBottom: 0, color: "var(--ink-3)" }}>
            {error}
          </p>
        </div>
      </div>
    );
  }

  if (!cfg || !clock) {
    return (
      <div className="bare">
        <div className="hint" style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <span className="spinner" /> Loading console…
        </div>
      </div>
    );
  }

  const hasSchedule = appts.length > 0;
  const isCoordinator = session.role === "coordinator";
  const initials = session.display_name
    .replace(/·.*$/, "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

  return (
    <div className="shell">
      <header className="topbar">
        <button
          className="btn btn-icon btn-ghost rail-toggle"
          onClick={() => setRailOpen((o) => !o)}
          aria-label="Show the disruption panel"
          title="Disruptions and replanning"
        >
          <Icon name="menu" size={17} />
        </button>

        <span className="brand">
          <span className="brand-mark"><BrandMark size={17} /></span>
          <span className="wordmark">
            <b>Panelist</b>
            <span>Coordinator console</span>
          </span>
        </span>

        <span
          className="pill hide-md"
          title={
            !hasSchedule
              ? "Nothing is scheduled yet"
              : solver && !solver.optimal
                ? "The solver hit its time limit and returned the best schedule it had found"
                : "The solver proved this schedule optimal"
          }
        >
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

        <div className="pop-wrap">
          <button
            className="btn btn-icon btn-ghost"
            onClick={() => setHelpOpen((o) => !o)}
            aria-label="How to read this console"
            aria-expanded={helpOpen}
            title="How to read this console"
          >
            <Icon name="help" size={17} />
          </button>
          {helpOpen && (
            <>
              <div className="pop-scrim" onClick={() => setHelpOpen(false)} />
              <div className="pop" role="dialog" aria-label="How to read this console">
                <h3>How this console works</h3>
                <p className="hint" style={{ marginTop: 2 }}>
                  Three steps, left to right.
                </p>
                <ol className="steps" style={{ marginTop: 14 }}>
                  <li>
                    <strong>Build the schedule.</strong> The solver places every
                    shortlisted interview into a room and a slot without
                    clashing a student, a room or a panel.
                  </li>
                  <li>
                    <strong>Tell it what went wrong.</strong> Queue everything
                    that happened in the left panel — together, not one at a
                    time.
                  </li>
                  <li>
                    <strong>Review the fix.</strong> The proposal is drawn onto
                    the board first. Nothing is saved until you apply it.
                  </li>
                </ol>
                <div style={{ marginTop: 14, paddingTop: 13, borderTop: "1px solid var(--line)" }}>
                  <span className="label">On the board</span>
                  <p className="hint" style={{ marginTop: 7, marginBottom: 0 }}>
                    Rooms run across the top, time down the side. Block colour
                    is the company&rsquo;s tier, not its identity. Click any
                    interview to trace that student across the day; press{" "}
                    <code>Esc</code> to clear.
                  </p>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="seg hide-sm" role="group" aria-label="Theme">
          {([
            ["light", "sun", "Light"],
            ["system", "auto", "Auto"],
            ["dark", "moon", "Dark"],
          ] as const).map(([m, ico, lbl]) => (
            <button
              key={m}
              aria-pressed={theme === m}
              aria-label={`${lbl} theme`}
              onClick={() => setTheme(m)}
              title={`${lbl} theme`}
            >
              <Icon name={ico} size={14} />
            </button>
          ))}
        </div>

        <span className="pill who-pill" title={`Signed in as ${session.username}`}>
          <span className="avatar" aria-hidden>{initials}</span>
          <span className="hide-md">{session.display_name}</span>
          {!isCoordinator && <span className="tag">Read only</span>}
        </span>

        <button className="btn btn-icon btn-ghost" onClick={signOut} title="Sign out" aria-label="Sign out">
          <Icon name="logout" size={16} />
        </button>

        <button
          className="btn btn-primary"
          onClick={solve}
          disabled={!!busy || !isCoordinator}
          title={!isCoordinator
            ? "Building the schedule needs a coordinator account"
            : "Solve the whole placement week from scratch"}
        >
          {busy === "Solving"
            ? <><span className="spinner" />Solving…</>
            : hasSchedule ? "Rebuild schedule" : "Build schedule"}
        </button>
      </header>

      {metrics ? (
        <MetricsBand m={metrics} />
      ) : busy === "Solving" ? (
        <MetricsSkeleton />
      ) : (
        <div className="metrics">
          <div className="metric" style={{ flex: "0 0 auto", minWidth: 340 }}>
            <span className="label">Getting started</span>
            <div className="metric-value" style={{ fontSize: 15, fontWeight: 560, letterSpacing: 0 }}>
              No schedule built yet
            </div>
            <div className="metric-sub" style={{ marginTop: 6 }}>
              Press <strong>Build schedule</strong> to solve the placement week.
            </div>
          </div>
        </div>
      )}

      <div className={`body${proposal ? " with-diff" : ""}`}>
        {railOpen && <div className="drawer-scrim" onClick={() => setRailOpen(false)} />}

        <div className={`rail${railOpen ? " open" : ""}`}>
          <button className="rail-close" onClick={() => setRailOpen(false)}>
            <Icon name="close" size={14} />
            Close
          </button>

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
            <div className={`step-head${queue.length ? " on" : ""}`}>
              <span className="step-n">3</span>
              <span className="step-t">Find a fix</span>
            </div>

            {/* Inputs to the button below and nothing else, so they sit with
                it rather than in the top bar. */}
            <div className="field">
              <span className="label">
                Move limit — how much of the week a fix may reshuffle
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  className="input"
                  type="range"
                  min={1}
                  max={50}
                  style={{ padding: 0, border: "none", background: "none", accentColor: "var(--accent)" }}
                  value={churnCap}
                  onChange={(e) => setChurnCap(Number(e.target.value))}
                  aria-label="Move limit percent"
                />
                <span className="num" style={{ width: 44, textAlign: "right", fontSize: 13 }}>
                  {churnCap}%
                </span>
              </div>
              <p className="hint" style={{ marginTop: 4, fontSize: 11.5 }}>
                Past this, a fix needs your explicit sign-off.
              </p>
            </div>

            <div className="field">
              <span className="label">Time now — everything earlier is locked</span>
              <select
                className="select"
                aria-label="Time now"
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
            </div>

            <button
              className="btn btn-primary btn-lg"
              style={{ width: "100%", marginTop: 4 }}
              onClick={propose}
              disabled={!queue.length || !!busy || !hasSchedule}
              title={!hasSchedule
                ? "Build a schedule first — there is nothing to replan"
                : !queue.length
                  ? "Add at least one event above"
                  : undefined}
            >
              {busy === "Replanning"
                ? <><span className="spinner" />Working out a fix…</>
                : queue.length
                  ? `Replan around ${queue.length} change${queue.length === 1 ? "" : "s"}`
                  : "Replan"}
            </button>
            <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
              You&rsquo;ll get a proposal to review first. Nothing on the
              schedule changes until you approve it.
            </p>
          </div>

          <details className="fold">
            <summary>
              <Icon name="sliders" size={15} style={{ color: "var(--ink-3)" }} />
              <span style={{ fontSize: 13, fontWeight: 570 }}>Priority overrides</span>
              {Object.keys(priority).length > 0 && (
                <span className="tag accent">{Object.keys(priority).length}</span>
              )}
              <Icon name="chevron" size={13} className="chev" />
            </summary>
            <div className="fold-body">
              <p className="hint" style={{ marginTop: 0, marginBottom: 10 }}>
                When there isn&rsquo;t room for everyone the solver drops niche
                companies before mass recruiters. Override that here — it applies
                to the next build and the next replan.
              </p>
              <div className="field">
                <select
                  className="select"
                  aria-label="Add a priority override"
                  value=""
                  onChange={(e) => {
                    const id = e.target.value;
                    if (id) setPriority((p) => ({ ...p, [id]: "protect" }));
                  }}
                >
                  <option value="">Add a company…</option>
                  {cfg.companies
                    .filter((c) => !priority[c.id])
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} · tier {c.tier}
                      </option>
                    ))}
                </select>
              </div>
              {Object.keys(priority).length === 0 ? (
                <p className="hint" style={{ marginBottom: 0 }}>
                  None set — the solver&rsquo;s own tier order applies.
                </p>
              ) : (
                <div className="queue">
                  {Object.entries(priority).map(([id, level]) => (
                    <div className="queue-item" key={id} style={{ borderLeftColor: "var(--accent)" }}>
                      <span style={{ flex: 1, minWidth: 0 }}>{companyName(id)}</span>
                      <div className="seg" role="group" aria-label="Priority">
                        {(["protect", "deprioritise"] as const).map((lv) => (
                          <button
                            key={lv}
                            aria-pressed={level === lv}
                            title={lv === "protect"
                              ? "Keep this company's interviews ahead of the tier order"
                              : "Drop this company first when capacity is short"}
                            onClick={() =>
                              setPriority((p) => ({ ...p, [id]: lv as PriorityLevel }))
                            }
                          >
                            {lv === "protect" ? "Protect" : "Drop first"}
                          </button>
                        ))}
                      </div>
                      <button
                        className="x"
                        aria-label="Remove override"
                        onClick={() =>
                          setPriority(({ [id]: _drop, ...rest }) => rest)
                        }
                      >
                        <Icon name="close" size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </details>

          {atRisk.length > 0 && (
            <details className="fold">
              <summary>
                <Icon name="warning" size={15} style={{ color: "var(--st-moved)" }} />
                <span style={{ fontSize: 13, fontWeight: 570 }}>
                  At risk on Day {day + 1}
                </span>
                <span className="tag">{atRisk.length}</span>
                <Icon name="chevron" size={13} className="chev" />
              </summary>
              <div className="fold-body">
                <p className="hint" style={{ marginTop: 0, marginBottom: 10 }}>
                  Students with a full day or no gap between interviews. A delay
                  here cascades furthest — these are who to check first.
                </p>
                {atRisk.slice(0, 8).map((r) => (
                  <button
                    className="risk-row"
                    key={r.student_id}
                    aria-pressed={focusStudent === r.student_id}
                    title={r.tight
                      ? `${r.tight} back-to-back interview(s) with no gap`
                      : `${r.count} interviews this day`}
                    onClick={() =>
                      setFocusStudent((s) =>
                        s === r.student_id ? null : r.student_id)
                    }
                  >
                    <span className="num" style={{ fontSize: 12 }}>{r.student_id}</span>
                    <span className={`n${r.tight > 0 ? " bad" : ""}`}>
                      {r.count}
                      {r.tight > 0 && ` · ${r.tight} tight`}
                    </span>
                  </button>
                ))}
              </div>
            </details>
          )}

          {versions.length > 0 && (
            <details className="fold">
              <summary>
                <Icon name="history" size={15} style={{ color: "var(--ink-3)" }} />
                <span style={{ fontSize: 13, fontWeight: 570 }}>
                  What&rsquo;s changed this week
                </span>
                <span className="tag">
                  v{versions.length}
                </span>
                <Icon name="chevron" size={13} className="chev" />
              </summary>
              <div className="fold-body">
                <p className="hint" style={{ marginTop: 0, marginBottom: 10 }}>
                  Each replan writes a new version rather than overwriting the
                  last, so the plan that existed before a disruption is still
                  there.
                </p>

                {history.slice(0, 5).map((h, i) => (
                  <div className="risk-row" key={`${h.applied_at}-${i}`}
                       title={h.descriptions.join("\n")}
                       style={{ alignItems: "flex-start" }}>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      {h.descriptions[0] ?? "Replan applied"}
                      <br />
                      <span className="hint" style={{ fontSize: 11.5 }}>
                        {new Date(h.applied_at).toLocaleTimeString([], {
                          hour: "2-digit", minute: "2-digit",
                        })}
                        {h.forced_churn > 0 && ` · ${h.forced_churn} cancelled`}
                      </span>
                    </span>
                    <span className={`n${h.cap_exceeded ? " bad" : ""}`} title="interviews moved">
                      {h.elective_churn}
                      {h.cap_exceeded && " !"}
                    </span>
                  </div>
                ))}

                {history.length === 0 && (
                  <p className="hint" style={{ marginBottom: 8 }}>
                    No replans applied yet — the board is the original solve.
                  </p>
                )}

                <div className="legend" style={{ marginTop: 10 }}>
                  {versions.slice(0, 6).map((v) => (
                    <span className="legend-item" key={v.version}
                          title={`${v.appointments} appointments · ${v.solver_status ?? "—"}`}>
                      <span className={`swatch ${v.origin === "replan" ? "moved" : "added"}`} />
                      v{v.version} {v.origin}
                      {v.is_current && " · live"}
                    </span>
                  ))}
                </div>
              </div>
            </details>
          )}

          {diag && diag.unscheduled > 0 && (
            <details className="fold">
              <summary>
                <Icon name="alert" size={15} style={{ color: "var(--st-cut)" }} />
                <span style={{ fontSize: 13, fontWeight: 570 }}>Can&rsquo;t be placed</span>
                <span className="tag" style={{ color: "var(--st-cut)" }}>{diag.unscheduled}</span>
                <Icon name="chevron" size={13} className="chev" />
              </summary>
              <div className="fold-body">
                {diag.capacity?.structural_shortfall && (
                  <p className="hint" style={{ marginTop: 0, marginBottom: 10 }}>
                    There is {diag.capacity.load_ratio}× more demand than there
                    are rooms and hours. No schedule can fit them all — these are
                    the companies worst affected.
                  </p>
                )}
                {diag.per_company.slice(0, 8).map((c) => (
                  <div className="risk-row" key={c.company_id} title={c.reason}>
                    <span>{c.company}</span>
                    <span className="n bad">
                      {c.unscheduled}/{c.demand}
                    </span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>

        <main className="main">
          <div className="toolbar">
            <div className="tabs" role="tablist" aria-label="Day">
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
            </div>

            <span className="spacer" />

            {proposal?.ok && (
              <span className="banner">
                <Icon name="alert" size={13} />
                Previewing a proposed fix — not saved
              </span>
            )}

            {focusStudent && (
              <button
                className="pill"
                onClick={() => setFocusStudent(null)}
                title="Stop tracing this student (Esc)"
                style={{ cursor: "pointer" }}
              >
                Tracing <strong className="num">{focusStudent}</strong>
                <span style={{ color: "var(--ink-3)" }}>
                  · {focusCount} on Day {day + 1}
                </span>
                <Icon name="close" size={12} />
              </button>
            )}

            <label className="ctl hide-sm" title="Show a single room's column">
              <Icon name="room" size={14} style={{ color: "var(--ink-3)" }} />
              <select
                className="select"
                style={{ width: 124 }}
                value={roomFilter ?? ""}
                onChange={(e) => setRoomFilter(e.target.value || null)}
                aria-label="Filter by room"
              >
                <option value="">All rooms</option>
                {cfg.rooms.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </label>

            <label className="ctl hide-sm" title="Highlight one company across the day">
              <Icon name="grid" size={14} style={{ color: "var(--ink-3)" }} />
              <select
                className="select"
                style={{ width: 140 }}
                value={companyFilter ?? ""}
                onChange={(e) => setCompanyFilter(e.target.value || null)}
                aria-label="Filter by company"
              >
                <option value="">All companies</option>
                {cfg.companies.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>

            <div className="seg hide-sm" role="group" aria-label="Board density">
              {(["comfortable", "compact"] as const).map((d) => (
                <button
                  key={d}
                  aria-pressed={density === d}
                  onClick={() => setDensity(d)}
                  title={d === "compact"
                    ? "Fit more rooms on screen"
                    : "More room for labels"}
                >
                  {d === "comfortable" ? "Roomy" : "Compact"}
                </button>
              ))}
            </div>
          </div>

          {hasSchedule ? (
            <div className="board">
              <ScheduleGrid
                day={day}
                cfg={cfg}
                clock={clock}
                appointments={boardAppts}
                companyName={companyName}
                changeState={changeState}
                lockedBefore={nowSlot}
                focusStudent={focusStudent}
                roomFilter={roomFilter}
                companyFilter={companyFilter}
                roomUtilisation={metrics?.room_utilization_per_room}
                rowH={geom.rowH}
                colW={geom.colW}
                gutter={geom.gutter}
                onPick={(a) =>
                  setFocusStudent((s) => (s === a.student_id ? null : a.student_id))
                }
              />

              <div className="legend-bar">
                <span className="label">Company tier</span>
                <div className="legend">
                  <span className="legend-item"><span className="swatch t1" /> Mass recruiter</span>
                  <span className="legend-item"><span className="swatch t2" /> Mid-size</span>
                  <span className="legend-item"><span className="swatch t3" /> Niche</span>
                </div>
                <span className="legend-sep" />
                <span className="label">During a replan</span>
                <div className="legend">
                  <span className="legend-item"><span className="swatch moved" /> ⇅ Moved</span>
                  <span className="legend-item"><span className="swatch cut" /> × Cancelled</span>
                  <span className="legend-item"><span className="swatch added" /> + Added</span>
                </div>
                <span className="spacer" />
                <span className="hint hide-md" style={{ fontSize: 11.5 }}>
                  Click an interview to trace that student across the day
                </span>
              </div>
            </div>
          ) : (
            <div className="bare">
              <div className="bare-card">
                <span className="bare-ico"><Icon name="grid" size={19} /></span>
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
            canApply={isCoordinator}
            clock={clock}
            companyName={companyName}
            applying={busy === "Applying"}
            onApply={applyProposal}
            onReject={() => setProposal(null)}
            onHoverStudent={setFocusStudent}
          />
        )}
      </div>

      {/* A toast rather than a toolbar chip: these run a sentence long. */}
      {error && cfg && (
        <div className="toast-stack">
          <div className="toast err" role="alert">
            <Icon name="alert" size={15} style={{ color: "var(--st-cut)", marginTop: 1 }} />
            <span style={{ flex: 1 }}>{error}</span>
            <button className="x" onClick={() => setError(null)} aria-label="Dismiss">
              <Icon name="close" size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Reduce an API error to one line a coordinator can act on.
 *
 * Every branch must end at a string: FastAPI puts the payload under `detail`,
 * and this API raises structured bodies as well as plain ones (the 409 for a
 * stale dataset, the 422 for an unsolvable week). An object returned from here
 * would throw when React rendered it.
 */
function describeError(e: unknown): string {
  if (!(e instanceof ApiError)) {
    return e instanceof Error ? e.message : String(e);
  }
  const detail =
    isRecord(e.detail) && "detail" in e.detail ? e.detail.detail : e.detail;

  if (typeof detail === "string") return detail;
  // Pydantic reports request-validation failures as a list of problems.
  if (Array.isArray(detail)) {
    const first = detail.find(isRecord);
    if (first && typeof first.msg === "string") return first.msg;
  }
  if (isRecord(detail)) {
    // The stale-dataset 409 writes the remedy into `message`.
    if (typeof detail.message === "string") return detail.message;
    // The solver's own note distinguishes a timeout from a real
    // infeasibility.
    if (isRecord(detail.solver) && typeof detail.solver.note === "string") {
      return detail.solver.note;
    }
  }
  return `API error ${e.status}`;
}
