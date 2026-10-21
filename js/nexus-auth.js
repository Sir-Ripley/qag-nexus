// QAG Nexus — Supabase magic-link auth + Researcher tier client (static-site drop-in).
//
// Usage (added at deploy time, NOT before the Supabase project exists):
//   1. In <head> or before this script:
//        <script>window.NEXUS_SUPABASE = { url: "https://xyzcompany.supabase.co", anonKey: "eyJ..." };</script>
//   2. <script type="module" src="/js/nexus-auth.js"></script>
//   3. Import what the page needs:
//        import { currentSession, signInWithEmail, signOut, fetchTelemetry, queueFit } from "/js/nexus-auth.js";
//
// Design: passwordless magic links (no passwords stored, no OAuth apps).
// The vessel dashboard keeps its demo feed for signed-out visitors and swaps
// to the hosted telemetry endpoint when a session exists.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function config() {
  const cfg = (typeof window !== "undefined" && window.NEXUS_SUPABASE) || {};
  if (!cfg.url || !cfg.anonKey) {
    throw new Error("NEXUS_SUPABASE is not configured — paid tier not wired yet.");
  }
  return cfg;
}

let client = null;
export function nexusClient() {
  if (!client) {
    const cfg = config();
    client = createClient(cfg.url, cfg.anonKey);
  }
  return client;
}

function functionsBase() {
  return config().url.replace(/\/$/, "") + "/functions/v1";
}

/** The current session, or null when signed out. */
export async function currentSession() {
  const { data, error } = await nexusClient().auth.getSession();
  if (error) throw error;
  return data.session ?? null;
}

/** Sends the magic link. The user clicks it in their email and lands signed in. */
export async function signInWithEmail(email) {
  const { error } = await nexusClient().auth.signInWithOtp({
    email,
    options: {
      // Where the magic link lands after verification:
      emailRedirectTo: window.location.origin + "/vessel.html",
    },
  });
  if (error) throw error;
}

export async function signOut() {
  const { error } = await nexusClient().auth.signOut();
  if (error) throw error;
}

/**
 * Hosted telemetry for the vessel dashboard.
 * Returns null when signed out (caller keeps the demo feed).
 * Otherwise { tier: 'free'|'researcher', display_name, fits[], progress[] }.
 */
export async function fetchTelemetry() {
  const session = await currentSession();
  if (!session) return null;
  const res = await fetch(functionsBase() + "/telemetry", {
    headers: { Authorization: "Bearer " + session.access_token },
  });
  if (res.status === 401) {
    await signOut();
    return null;
  }
  if (!res.ok) throw new Error("telemetry endpoint returned " + res.status);
  return res.json();
}

/**
 * Queue a SPARC optimization run. Throws "Researcher tier required…" for
 * free users (the database refuses the insert — this is the paywall).
 */
export async function queueFit(datasetVersion, params) {
  const session = await currentSession();
  if (!session) throw new Error("Sign in first — then queue your fit.");
  const res = await fetch(functionsBase() + "/telemetry/fits", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + session.access_token,
    },
    body: JSON.stringify({
      dataset_version: datasetVersion || "sparc-2024.1",
      params: params || {},
    }),
  });
  if (res.status === 403) {
    throw new Error("Researcher tier required to queue fits — see the Researcher Tier page.");
  }
  if (!res.ok) throw new Error("queueFit failed with " + res.status);
  return res.json();
}

/**
 * Minimal sign-in UI helper. Call with a container element id; renders an
 * email input + button, and a signed-in state with tier + sign-out.
 * Pages can use this as-is or build their own on the functions above.
 */
export async function mountAuthWidget(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const render = async () => {
    let session = null;
    try {
      session = await currentSession();
    } catch (_) {
      el.innerHTML = "<p class='honest'>Account system wiring in progress.</p>";
      return;
    }
    if (!session) {
      el.innerHTML =
        "<form id='nexus-signin' class='nexus-auth'>" +
        "<label>Researcher sign-in <input type='email' id='nexus-email' placeholder='you@lab.edu' required></label>" +
        "<button type='submit'>Email me a sign-in link</button>" +
        "<p class='honest' id='nexus-auth-msg'>Passwordless — a link lands in your inbox, no password to remember.</p>" +
        "</form>";
      document.getElementById("nexus-signin").addEventListener("submit", async (e) => {
        e.preventDefault();
        const msg = document.getElementById("nexus-auth-msg");
        try {
          await signInWithEmail(document.getElementById("nexus-email").value.trim());
          msg.textContent = "Check your inbox — the sign-in link is on its way.";
        } catch (err) {
          msg.textContent = "Couldn't send the link: " + err.message;
        }
      });
    } else {
      let tier = "free";
      try {
        const t = await fetchTelemetry();
        tier = (t && t.tier) || "free";
      } catch (_) { /* dashboard payload failed; still show signed-in state */ }
      const pill = tier === "researcher"
        ? "<span class='pill live'>RESEARCHER</span>"
        : "<span class='pill soon'>FREE</span>";
      el.innerHTML =
        "<div class='nexus-auth'>" + pill +
        "<span> " + escapeHtml(session.user.email || "") + "</span> " +
        "<button id='nexus-signout'>Sign out</button></div>";
      document.getElementById("nexus-signout").addEventListener("click", async () => {
        await signOut();
        render();
      });
    }
  };
  render();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
