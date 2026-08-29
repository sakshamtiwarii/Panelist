"use client";

import { useState } from "react";
import { ApiError, api, type Session } from "@/lib/api";
import Icon, { BrandMark } from "./Icon";
import BoardSpecimen from "./BoardSpecimen";

/**
 * Sign-in gate. Split rather than a centred card, so the left half can say what
 * the console is before asking to be let into it — including a small specimen
 * of the real board, drawn from the same tokens.
 *
 * The demo credentials are on the form deliberately: this runs over a synthetic
 * dataset. Clicking a card fills the form rather than signing in, so it is
 * visible which account is in use.
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
        // No HTTP response at all. The request is same-origin, so reaching here
        // means the page's own server is gone — a dev server that died
        // mid-session.
        setError(
          "Lost contact with the console's own server. Start it with " +
            "`docker compose up`, then reload this page.",
        );
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <section className="auth-aside">
       <div className="auth-aside-inner">
        <div className="auth-brand">
          <span className="brand-mark"><BrandMark size={18} /></span>
          <b>Panelist</b>
        </div>

        <p className="auth-lede">
          Every shortlisted interview placed into <strong>a room and a
          slot</strong> — without clashing a student, a room or a panel. When
          the day goes wrong, replan around it and see what the fix costs
          before you commit to it.
        </p>

        <figure className="auth-figure">
          <BoardSpecimen />
          <figcaption>
            <b>Day 1 of the placement week.</b> Rooms across the top, time down
            the side; a block&rsquo;s colour is the company&rsquo;s tier. The
            amber one is an interview the last replan moved.
          </figcaption>
        </figure>
       </div>
      </section>

      <section className="auth-form">
        <div className="auth-form-inner">
          <h2>Sign in</h2>
          <p className="hint" style={{ marginTop: 4 }}>
            Coordinator console for the 2026 placement week.
          </p>

          <form onSubmit={submit} style={{ marginTop: 22 }}>
            <div className="field">
              <span className="label">Username</span>
              <input
                className="input"
                aria-label="Username"
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
                aria-label="Password"
                value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && (
              <div className="callout err" style={{ margin: "0 0 12px" }}>
                {error}
              </div>
            )}
            <button
              className="btn btn-primary btn-lg"
              style={{ width: "100%" }}
              disabled={busy || !username || !password}
            >
              {busy ? <><span className="spinner" />Signing in…</> : "Sign in"}
            </button>
          </form>

          <div style={{ marginTop: 24, paddingTop: 18, borderTop: "1px solid var(--line)" }}>
            <span className="label">Demo accounts — click to fill</span>
            <div style={{ display: "grid", gap: 7, marginTop: 10 }}>
              {ACCOUNTS.map((a) => (
                <button
                  key={a.username}
                  type="button"
                  className="demo-card"
                  title={a.blurb}
                  onClick={() => {
                    setUsername(a.username);
                    setPassword(a.password);
                    setError(null);
                  }}
                >
                  <span>
                    <b>{a.role}</b>
                    <small>{a.username} · {a.password}</small>
                  </span>
                  <Icon name="arrowRight" size={15} className="go" />
                </button>
              ))}
            </div>
            <p className="hint" style={{ marginTop: 10, marginBottom: 0, fontSize: 11.5 }}>
              A viewer can do everything except commit a change to the live
              schedule.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
