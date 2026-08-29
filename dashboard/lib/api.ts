// Relative in every environment — next.config.mjs supplies "/api" and the
// route handler behind it forwards to the solver. Absolute only when someone
// deliberately points the console at an API directly.
const BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    // The session is an httpOnly cookie. Harmless while the call is
    // same-origin, and required the moment someone points BASE elsewhere.
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    let detail: unknown = await res.text();
    try { detail = JSON.parse(detail as string); } catch { /* plain text */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

export const api = {
  login: (username: string, password: string) =>
    call<Session>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => call<{ signed_out: boolean }>("/auth/logout", { method: "POST" }),
  me: () => call<Session>("/auth/me"),
  config: () => call<ConfigResponse>("/config"),
  health: () => call<Health>("/health"),
  generate: (body: GenerateBody) =>
    call<GenerateResponse>("/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  solve: (dataset: string, timeLimit: number,
          priorityOverrides: PriorityOverrides = {}) =>
    call<SolveResponse>("/schedule", {
      method: "POST",
      body: JSON.stringify({
        dataset, time_limit_seconds: timeLimit,
        priority_overrides: priorityOverrides,
      }),
    }),
  schedule: () => call<{ count: number; appointments: Appointment[] }>("/schedule"),
  metrics: () => call<Metrics>("/metrics"),
  diagnostics: () => call<Diagnostics>("/diagnostics"),
  versions: () => call<{ versions: ScheduleVersion[] }>("/schedule/versions"),
  history: () => call<{ events: ReplanEvent[] }>("/replan/history"),
  propose: (body: ReplanBody) =>
    call<Proposal>("/replan", { method: "POST", body: JSON.stringify(body) }),
  apply: (proposalId: string, useAlternative = false) =>
    call<ApplyResponse>("/replan/apply", {
      method: "POST",
      body: JSON.stringify({
        proposal_id: proposalId, use_alternative: useAlternative,
      }),
    }),
};

/* ---- types ------------------------------------------------------------- */

export interface Session {
  username: string;
  display_name: string;
  role: "coordinator" | "viewer";
}

export interface Health {
  status: string;
  dataset_loaded: string | null;
  has_schedule: boolean;
}

export interface GenerateBody {
  name: string;
  seed: number;
  companies: number;
  students: number;
  rooms: number;
  days: number;
  /** Omit for the natural (oversubscribed) instance; 0.9 is hard but solvable. */
  load_factor?: number;
}

export interface GenerateResponse {
  dataset: string;
  seed: number;
  path: string;
  density_report: string;
}

export interface GridConfig {
  days: number;
  slot_minutes: number;
  usable_slots_per_day: number[];
  slots_per_day_count: number;
  lunch: [string, string];
}

export interface Room {
  id: string;
  name: string;
  blocked_windows: { day: number; from_slot: number; to_slot: number; reason: string }[];
}

export interface Company {
  id: string;
  name: string;
  tier: number;
  panel_count: number;
  interview_minutes: number;
  shortlist_size: number;
  cgpa_cutoff: number;
}

export interface ConfigResponse {
  config: GridConfig;
  slots_per_day_raw: number;
  day_start_minutes: number;
  rooms: Room[];
  companies: Company[];
}

export interface Appointment {
  id: string;
  company_id: string;
  student_id: string;
  duration_slots: number;
  tier: number;
  start: number;
  end: number;
  day: number;
  slot: number;
  room: string | null;
  panel: number;
}

/** One entry in the schedule's version history. */
export interface ScheduleVersion {
  version: number;
  origin: string;          // "solve" | "replan"
  solver_status: string | null;
  is_current: boolean;
  created_at: string;
  appointments: number;
}

/** One applied replan: what caused it and what it cost. */
export interface ReplanEvent {
  applied_at: string;
  descriptions: string[];
  elective_churn: number;
  forced_churn: number;
  churn_pct: number;
  cap_exceeded: boolean;
  notify_count: number;
  schedule_id: number | null;
}

export interface Metrics {
  interviews_total: number;
  interviews_scheduled: number;
  interviews_unscheduled: number;
  pct_scheduled: number;
  student_clashes: number;
  room_utilization_pct: number;
  /** Per room, keyed by room id — the aggregate above is only half of it. */
  room_utilization_per_room: Record<string, number>;
  avg_student_wait_minutes: number;
  max_student_wait_minutes: number;
  students_with_interviews: number;
  avg_interviews_per_student: number;
}

export interface SolverReport {
  status: string;
  usable: boolean;
  optimal: boolean;
  note: string;
  timed_out: boolean;
  wall_time_seconds: number;
}

export interface SolveResponse {
  solver: SolverReport;
  metrics: Metrics;
  verification_errors: string[];
  scheduled: number;
  unscheduled: number;
}

export interface Diagnostics {
  unscheduled: number;
  capacity: {
    headline: string;
    structural_shortfall: boolean;
    load_ratio: number;
    max_schedulable_estimate: number | null;
    room_utilization_per_day_pct: Record<string, number>;
    saturated_windows: { text: string }[];
  } | null;
  per_company: {
    company_id: string;
    company: string;
    unscheduled: number;
    demand: number;
    dominant_cause: string;
    reason: string;
  }[];
}

export interface DisruptionEvent {
  type: string;
  company_id?: string;
  student_id?: string;
  room_id?: string;
  day?: number;
  hours?: number;
  count?: number;
  scope?: string;
  from_slot?: number;
  to_slot?: number;
  reason?: string;
  // roster amendments
  name?: string;
  tier?: number;
  cgpa_cutoff?: number;
  panel_count?: number;
  interview_minutes?: number;
  shortlist?: string[];
  shortlist_size?: number;
}

/**
 * The coordinator's exceptions to the solver's tier priority, company_id ->
 * level. "normal" is the same as saying nothing; the API strips it.
 */
export type PriorityLevel = "protect" | "normal" | "deprioritise";
export type PriorityOverrides = Record<string, PriorityLevel>;

export interface ReplanBody {
  disruptions: DisruptionEvent[];
  churn_cap_pct: number;
  time_limit_seconds: number;
  now_slot?: number | null;
  priority_overrides?: PriorityOverrides;
}

export interface ApplyResponse {
  applied: boolean;
  appointments: number;
  churn: number;
  version: number;
  applied_alternative: boolean;
  label: string;
}

/** The lower-churn option, summarised enough to choose between the two. */
export interface AlternativeSummary {
  label: string;
  elective_churn_count: number;
  elective_churn_pct: number;
  forced_churn_count: number;
  unscheduled: number;
  solver: SolverReport;
  notify_count: number;
  verification_errors: string[];
}

export interface ChangeDetail {
  id: string;
  company_id: string;
  student_id: string;
  /** Carried so a newly placed interview can be drawn, not just named. */
  duration_slots: number;
  tier: number;
  from?: { day: number; slot: number; room: string | null; panel: number };
  to?: { day: number; slot: number; room: string | null; panel: number };
}

export interface Proposal {
  proposal_id: string | null;
  ok: boolean;
  reason?: string;
  timed_out?: boolean;
  lock_conflicts?: string[];
  label?: string;
  disruptions_applied: string[];
  solver?: SolverReport;
  diff?: {
    added: string[];
    removed: string[];
    moved: string[];
    forced_removed: string[];
    elective_removed: string[];
    forced_churn_count: number;
    elective_churn_count: number;
    elective_churn_pct: number;
    churn_count: number;
    churn_pct: number;
    baseline_appointments: number;
    added_detail: ChangeDetail[];
    removed_detail: ChangeDetail[];
    moved_detail: ChangeDetail[];
    affected_students: string[];
    affected_companies: string[];
  };
  notify?: {
    students: { student_id: string; urgency: string; changes: string[] }[];
    companies: { company_id: string; company: string; cancelled: number; moved: number; added: number }[];
    total_people_to_contact: number;
  };
  churn_cap_pct?: number;
  cap_exceeded?: boolean;
  churn_irreducible?: boolean;
  authorization_prompt?: string | null;
  verification_errors?: string[];
  unscheduled?: number;
  alternative?: AlternativeSummary | null;
  priority_overrides?: PriorityOverrides;
}
