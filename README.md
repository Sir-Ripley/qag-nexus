# QAG Nexus

The Quantum Affinity Gravity educational portal — https://qag-nexus.vercel.app/

A static site (no build step): 6 pages + a 4-module course (10 lessons) +
shared nav/styles. The Free Explorer tier teaches and simulates; the
Researcher Tier ($30/mo) adds the hosted compute pipeline — all live:

- **Accounts** — Supabase magic-link auth (project `klneswylibiihgdewznx`);
  profiles + tier stored in Postgres, guarded by row-level security and a
  database trigger that makes the tier writable only by the billing webhook.
- **Hosted fits** — researchers queue SPARC fits from the Vessel page; the
  telemetry edge function serves their tier, fits, and course progress.
- **Compute worker** — `worker/` drains the fit queue from GitHub Actions
  every 15 minutes, running the canonical QAGKinetics SPARC pipeline
  (certified: reproduces the published SPARC v3.0 sweep exactly).
- **Payments** — Stripe checkout is the final hookup: the webhook endpoint
  is deployed and inert until the Stripe secrets land.

## Pages

- `index.html` — home
- `learn.html` — learning paths
- `simulate.html` — interactive simulations
- `vessel.html` — the Command Vessel (demo feed + hosted pipeline panel)
- `research.html` — Researcher Tier + latest SPARC validation results
- `course/` — the 4-module course (10 lessons; Researcher lessons unlock
  in-page for researcher accounts)
- `js/nexus-auth.js` — the Supabase auth/telemetry client
- `worker/` — the compute worker (see `worker/README.md`)
- `nav.js`, `nexus.css` — shared navigation and styles

## Deploy

Git-connected Vercel project `qag-nexus` — push to `main` auto-deploys.
No build command; output directory is the repo root.

Contact: droiden.rr@gmail.com · R&R Droids
