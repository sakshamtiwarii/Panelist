"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  API_BASE, ApiError, api, diagnoseReachability,
  type Appointment, type ConfigResponse, type Diagnostics,
  type DisruptionEvent, type Metrics, type PriorityLevel,
  type PriorityOverrides, type Proposal, type Session, type SolverReport,
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
  // The coordinator's exceptions to the solver's tier priority. Held with the
  // request rather than on the company, because "protect this one through
  // THIS squeeze" is a decision about a solve, not a standing property.
  const [priority, setPriority] = useState<PriorityOverrides>({});
  const [focusStudent, setFocusStudent] = useState<string | null>(null);
  // Guide 2.4: the board is filterable by day / room / company. Day is the
  // tab strip; these two are the rest of it. Room narrows the columns,
  // company dims the rest so the surroundings stay readable.
  const [roomFilter, setRoomFilter] = useState<string | null>(null);
  const [companyFilter, setCompanyFilter] = useState<string | null>(null);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");
  // "origin-rejected" means the API answered but refused this page's origin —
  // almost always the wrong port, which otherwise looks like a dead backend.
  const [reach, setReach] = useState<"unreachable" | "origin-rejected" | null>(null);
  // The API is up and answering, but no dataset has been generated yet. A
  // fresh clone always lands here, because data/ is gitignored.
  const [needsDataset, setNeedsDataset] = useState(false);

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

  const load = useCallback(async () => {
    try {
      setCfg(await api.config());
      setNeedsDataset(false);
      setError(null);
      const h = await api.health();
      if (h.has_schedule) await refresh();
    } catch (e) {
      setError(describeError(e));
      // A 404 from /config means one specific thing: the API is healthy and
      // there is simply no dataset on disk yet. Reporting that as "nothing is
      // answering" sends the coordinator to restart a container that is fine.
      if (e instanceof ApiError && e.status === 404) setNeedsDataset(true);
      else if (isNetworkFailure(e)) setReach(await diagnoseReachability());
    }
  }, [refresh]);

  useEffect(() => {
    if (!session) return;
    void load();
  }, [session, load]);

  const signOut = async () => {
    try { await api.logout(); } catch { /* cookie may already be gone */ }
    setSession(null);
    setCfg(null);
    setAppts([]);
    setMetrics(null);
    setProposal(null);
    setQueue([]);
  };

  // A fresh clone has no data/ — it is gitignored, and generating it was a
  // curl the console gave no way to make. These are the settings the README
  // and CI both treat as the primary dataset.
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

  // While a proposal is open the board shows the PROPOSED placement, with
  // interviews the proposal would cancel kept visible in their old slot so
  // the loss is legible rather than a silent absence.
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
    // Interviews the proposal ADDS are not in `appts` at all — they do not
    // exist yet. Without synthesising them the board previewed an addition as
    // nothing whatsoever, while the legend advertised a colour for it.
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

  const perDayCount = useMemo(() => {
    const counts = new Map<number, number>();
    boardAppts.forEach((a) => counts.set(a.day, (counts.get(a.day) ?? 0) + 1));
    return counts;
  }, [boardAppts]);

  /**
   * Who a disruption would hurt most on this day, before one happens.
   *
   * Two things make a student fragile: a full day (any delay cascades through
   * all of it) and back-to-back interviews (no slack to absorb an overrun).
   * Both are read off the board rather than asked of the API, so the view
   * follows a proposal preview as well as the live schedule.
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
    return <div className="bare"><span className="hint">Checking session…</span></div>;
  }

  if (!session) {
    return <LoginScreen onSignedIn={setSession} />;
  }

  if (error && !cfg) {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return (
      <div className="bare">
        <div className="bare-card">
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
                    style={{ marginTop: 12 }}
                    onClick={generateStarter}
                    disabled={!!busy}
                  >
                    {busy === "Generating"
                      ? "Generating…"
                      : "Generate the starter dataset"}
                  </button>
                  <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
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
          ) : reach === "origin-rejected" ? (
            <>
              <h2>Wrong address for this console</h2>
              <p className="hint">
                The API at <code>{API_BASE}</code> is running, but it refuses
                requests from <code>{origin}</code> — so the browser blocks
                every response before this page can read it.
              </p>
              <p className="hint" style={{ marginTop: 10 }}>
                This almost always means the dashboard is published on a
                different port than the one you opened. Check which port
                Docker published:
              </p>
              <ol className="steps">
                <li>
                  Run <code>docker compose ps</code> and look at the{" "}
                  <code>dashboard</code> row — the host port is on the left of{" "}
                  <code>-&gt;3000</code>.
                </li>
                <li>Open that address instead, then hard-reload.</li>
                <li>
                  A port override lives in <code>.env</code>{" "}
                  (<code>PANELIST_WEB_PORT</code>); the API allows exactly that
                  origin.
                </li>
              </ol>
            </>
          ) : (
            <>
              <h2>Can&rsquo;t reach the scheduler</h2>
              <p className="hint">
                The console is running, but nothing is answering at{" "}
                <code>{API_BASE}</code>. Start it and this page will connect on
                reload.
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

          <div className="rail-section">
            <span className="label" style={{ display: "block", marginBottom: 6 }}>
              Priority overrides
            </span>
            <p className="hint" style={{ marginTop: 0, marginBottom: 10 }}>
              When there isn&rsquo;t room for everyone the solver drops niche
              companies before mass recruiters. Override that here — it applies
              to the next build and the next replan.
            </p>
            <div className="field">
              <select
                className="select"
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
                  <div className="queue-item" key={id}>
                    <span style={{ flex: 1 }}>{companyName(id)}</span>
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
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {atRisk.length > 0 && (
            <div className="rail-section">
              <span className="label">
                At risk on Day {day + 1} ({atRisk.length})
              </span>
              <p className="hint" style={{ marginTop: 6, marginBottom: 10 }}>
                Students with a full day or no gap between interviews. A delay
                here cascades furthest — these are who to check first.
              </p>
              {atRisk.slice(0, 6).map((r) => (
                <button
                  className="risk-row"
                  key={r.student_id}
                  style={{ width: "100%", textAlign: "left" }}
                  title={r.tight
                    ? `${r.tight} back-to-back interview(s) with no gap`
                    : `${r.count} interviews this day`}
                  onClick={() =>
                    setFocusStudent((s) =>
                      s === r.student_id ? null : r.student_id)
                  }
                >
                  <span>{r.student_id}</span>
                  <span className="n num">
                    {r.count}
                    {r.tight > 0 && ` · ${r.tight} tight`}
                  </span>
                </button>
              ))}
            </div>
          )}

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
            <label className="ctl" title="Show a single room's column">
              Room
              <select
                className="select"
                style={{ width: 116 }}
                value={roomFilter ?? ""}
                onChange={(e) => setRoomFilter(e.target.value || null)}
              >
                <option value="">All rooms</option>
                {cfg.rooms.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </label>
            <label className="ctl" title="Highlight one company across the day">
              Company
              <select
                className="select"
                style={{ width: 132 }}
                value={companyFilter ?? ""}
                onChange={(e) => setCompanyFilter(e.target.value || null)}
              >
                <option value="">All companies</option>
                {cfg.companies.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
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
              roomFilter={roomFilter}
              companyFilter={companyFilter}
              roomUtilisation={metrics?.room_utilization_per_room}
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

/** True for a network-level failure — no HTTP response came back at all. */
function isNetworkFailure(e: unknown) {
  return !(e instanceof ApiError);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Reduce an API error to one line a coordinator can act on.
 *
 * Every branch must end at a string. FastAPI puts the payload under `detail`,
 * and this API raises structured bodies as well as plain ones — the 409 for a
 * stored dataset that predates the time model carries `{error, message,
 * missing}`, and the 422 for an unsolvable week carries `{solver,
 * constraints}`. The previous version was typed as returning a string but
 * handed those objects straight back, and an object rendered as a React child
 * throws — so the two errors the backend works hardest to explain were
 * precisely the two that took the whole console down instead of showing.
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
    // For an unsolvable week the solver's own note explains it best: it is
    // the text that distinguishes a timeout from a real infeasibility.
    if (isRecord(detail.solver) && typeof detail.solver.note === "string") {
      return detail.solver.note;
    }
  }
  return `API error ${e.status}`;
}
