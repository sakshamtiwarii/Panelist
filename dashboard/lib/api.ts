const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
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
  config: () => call<ConfigResponse>("/config"),
  health: () => call<Health>("/health"),
  solve: (dataset: string, timeLimit: number) =>
    call<SolveResponse>("/schedule", {
      method: "POST",
      body: JSON.stringify({ dataset, time_limit_seconds: timeLimit }),
    }),
  schedule: () => call<{ count: number; appointments: Appointment[] }>("/schedule"),
  metrics: () => call<Metrics>("/metrics"),
  diagnostics: () => call<Diagnostics>("/diagnostics"),
  propose: (body: ReplanBody) =>
    call<Proposal>("/replan", { method: "POST", body: JSON.stringify(body) }),
  apply: (proposalId: string) =>
    call<{ applied: boolean; appointments: number; churn: number }>(
      "/replan/apply",
      { method: "POST", body: JSON.stringify({ proposal_id: proposalId }) },
    ),
};

/* ---- types ------------------------------------------------------------- */

export interface Health {
  status: string;
  dataset_loaded: string | null;
  has_schedule: boolean;
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

export interface Metrics {
  interviews_total: number;
  interviews_scheduled: number;
  interviews_unscheduled: number;
  pct_scheduled: number;
  student_clashes: number;
  room_utilization_pct: number;
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
}

export interface ReplanBody {
  disruptions: DisruptionEvent[];
  churn_cap_pct: number;
  time_limit_seconds: number;
  now_slot?: number | null;
}

export interface ChangeDetail {
  id: string;
  company_id: string;
  student_id: string;
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
  has_alternative?: boolean;
}
