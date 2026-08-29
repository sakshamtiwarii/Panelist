"use client";

import { useState } from "react";
import {
  API_BASE, ApiError, api, diagnoseReachability, type Session,
} from "@/lib/api";

/**
 * Sign-in gate.
 *
 * The demo credentials are shown on the form on purpose: this is an
 * evaluation build over a synthetic dataset, and a reviewer should not have to
 * dig through a README to get in. Clicking a card fills the form rather than
 * signing in directly, so the reviewer sees which account they are using.
 */

const ACCOUNTS = [
  {
    username: "coordinator",
    password: "placement2026",
    role: "Coordinator",
    blurb: "Full access — build the schedule, replan, apply fixes.",
  },
  {
    username: "viewer",
    password: "review2026",
    role: "Viewer",
    blurb: "Read-only — can request a fix, but not apply one.",
  },
];

export default function LoginScreen({
  onSignedIn,
}: {
  onSignedIn: (s: Session) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.login(username.trim(), password));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.status === 401
            ? "Incorrect username or password."
            : `The API replied ${err.status}. ${String(err.message).slice(0, 160)}`,
        );
      } else {
        // No HTTP response at all. Tell a dead API apart from an origin the
        // API refuses — the browser reports both identically, and the second
        // is nearly always the dashboard being opened on the wrong port.
        const why = await diagnoseReachability();
        const origin =
          typeof window !== "undefined" ? window.location.origin : "this page";
        setError(
          why === "origin-rejected"
            ? `The API at ${API_BASE} is running but refuses requests from ` +
              `${origin}. You are probably on the wrong port — check the ` +
              `dashboard row of \`docker compose ps\` and open that address.`
            : `Nothing is answering at ${API_BASE}. Start it with ` +
              `\`docker compose up\`, then reload.`,
        );
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bare">
      <div className="bare-card" style={{ maxWidth: 400 }}>
        <h2>Panelist</h2>
        <p className="hint" style={{ marginTop: 2 }}>
          Placement week coordinator console
        </p>

        <form onSubmit={submit} style={{ marginTop: 20 }}>
          <div className="field">
            <span className="label">Username</span>
            <input
              className="input"
              value={username}
              autoComplete="username"
              autoFocus
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="field">
            <span className="label">Password</span>
            <input
              className="input"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && (
            <div className="callout err" style={{ marginBottom: 12 }}>
              {error}
            </div>
          )}
          <button
            className="btn btn-primary btn-lg"
            style={{ width: "100%" }}
            disabled={busy || !username || !password}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div style={{ marginTop: 22, paddingTop: 16, borderTop: "1px solid var(--line)" }}>
          <span className="label">Demo accounts — click to fill</span>
          <div style={{ display: "grid", gap: 6, marginTop: 9 }}>
            {ACCOUNTS.map((a) => (
              <button
                key={a.username}
                type="button"
                className="kind"
                onClick={() => {
                  setUsername(a.username);
                  setPassword(a.password);
                  setError(null);
                }}
              >
                <strong>{a.role}</strong>
                <small>
                  {a.username} · {a.password}
                </small>
                <small>{a.blurb}</small>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
