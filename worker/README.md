# QAG Nexus compute worker

Executes the SPARC fits that Researcher-tier users queue from the Vessel page.
Before this worker existed, queued fits waited at `queued` forever; the site
said so honestly. This is the piece that drains the queue.

## What it does

1. Polls `public.fits` for `status='queued'` rows (oldest first).
2. Claims one **atomically** (conditional update `WHERE status='queued'`), so
   any number of worker processes can run safely.
3. Ingests SPARC rotation curves from the official `Rotmod_LTG.zip` release
   using the QAGKinetics Ingestion Engine v1.0.0 rules, and fits each galaxy
   with the QAG law — a verbatim port of `QAGKinetics/code/sweep_v3.py`
   (Υ★ = 0.5 fixed; per-galaxy least-squares over v∞ and r_aff; Newton and
   MOND scored on the same points).
4. Writes `status='done'` with the full result JSON (per-galaxy table +
   global χ²/dof/reduced-χ²/BIC per model, dataset sha256 for provenance),
   or `status='failed'` with the error. Nothing fails silently.

**Certification (2026-10-21):** a full local run reproduces the published
SPARC v3.0 numbers exactly — QAG red-χ² 5.549148, MOND 56.553877, Newton
616.741738; QAG beats MOND 167/175 and Newton 170/175; per-galaxy values
match `sparc175_corrected.csv` to the last printed digit. A full 175-galaxy
fit takes ~1 s of compute on modest hardware.

## Fit params

`fits.params` (jsonb, set by whoever queues the fit):

```json
{"galaxies": ["NGC3198", "DDO154"]}
```

Omit `galaxies` to fit the whole dataset. Unknown names fail the fit with a
clear error — no silent subsets. Supported `dataset_version`: `sparc-2024.1`.

## Run it

```bash
pip install -r requirements.txt

export SUPABASE_URL="https://klneswylibiihgdewznx.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="…"   # dashboard → Project Settings → API
                                       # (service_role secret — server-side
                                       #  only, never commit it, never paste
                                       #  it into chat)

python3 compute_worker.py --once        # drain the queue and exit (cron)
python3 compute_worker.py --poll 30     # resident worker, 30 s cadence
python3 compute_worker.py --fit-local NGC3198,DDO154   # engine self-test
```

## Hosting options

* **GitHub Actions** (recommended v1 host): `.github/workflows/nexus-worker.yml`
  in this folder wakes the worker every 15 minutes and on demand. Add the
  service-role key as the repo secret `SUPABASE_SERVICE_ROLE_KEY`
  (repo Settings → Secrets and variables → Actions). Worst-case pickup delay
  for a queued fit is ~15 minutes; fits themselves take seconds.
* **Any always-on box / VM**: `nexus-worker.service` (systemd example in this
  folder) with `--poll 30` — pickup delay ~30 s.
* **Cloud Run job**: same script, `--once`, triggered on a schedule — the
  scale-out path when fit volume grows.

## Known limitations (v1, deliberate)

* A worker killed mid-fit leaves its row at `running`; it stays visible as
  such. Requeue by setting the row back to `queued`.
* One fit at a time per process; scale by running more processes.
