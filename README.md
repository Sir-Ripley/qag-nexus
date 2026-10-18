# QAG Nexus

The Quantum Affinity Gravity educational portal — https://qag-nexus.vercel.app/

A static site (no build step): 6 pages + 4-lesson course + shared nav/styles.
Free tier teaches and simulates; the Researcher Tier page outlines the paid
cloud tier (Supabase auth + paywall — in build).

## Pages

- `index.html` — home
- `learn.html` — learning paths
- `simulate.html` — interactive simulations
- `vessel.html` — the Vessel project
- `research.html` — researcher tier + latest SPARC validation results
- `course.html` + `lessons/` — the 4-module course (10 lessons)
- `nav.js`, `nexus.css` — shared navigation and styles

## Deploy

Git-connected Vercel project `qag-nexus` — push to `main` auto-deploys.
No build command; output directory is the repo root.

## Roadmap

Supabase (auth + Postgres + RLS paywall) and Stripe — see
`SUPABASE_SETUP.md` in the build workspace. Contact link default:
droiden.rr@gmail.com.
