#!/usr/bin/env python3
"""
QAG Nexus — compute worker v1.0.0
==================================
Executes the fits that Researcher-tier users queue from the Vessel page.

Pipeline (identical math to the published SPARC v3.0 sweep):
  1. Poll public.fits for status='queued' rows (oldest first).
  2. Claim one atomically: PATCH status='running' WHERE id AND status='queued'
     (a lost race returns no row — the worker just moves on).
  3. Ingest SPARC rotation curves from the official Rotmod_LTG.zip release,
     using the QAGKinetics Ingestion Engine v1.0.0 parsing/quality rules
     (columns Rad/Vobs/errV/Vgas/Vdisk/Vbul; errV>0, Vobs>0, >=4 points).
  4. Fit each galaxy with the QAG law
        v(r) = sqrt( Upsilon* * (Vdisk^2 + Vbul^2) + Vgas^2
                     + v_inf^2 * (1 - exp(-r / r_aff)) ),   Upsilon* = 0.5
     via scipy least_squares (x0=[120, 3], bounds v_inf [0, 400],
     r_aff [0.1, 60]) and score Newton + MOND on the same points —
     a verbatim port of QAGKinetics code/sweep_v3.py.
  5. Write status='done' + the result JSON (per-galaxy table + global
     chi2/dof/red/BIC per model), or status='failed' + the error.

Fit params (fits.params jsonb):
  {"galaxies": ["NGC3198", "DDO154"]}   optional; default = every galaxy
                                        in the dataset
  Unknown galaxy names fail the fit with a clear error. No silent subsets.

Configuration (environment):
  SUPABASE_URL              e.g. https://<ref>.supabase.co   (required)
  SUPABASE_SERVICE_ROLE_KEY service-role key                (required —
                            server-side only, NEVER in the browser/repo)
  SPARC_ZIP_URL             default https://astroweb.cwru.edu/SPARC/Rotmod_LTG.zip
  SPARC_CACHE_DIR           default ~/.cache/qag-nexus-worker
  WORKER_ID                 default hostname

Modes:
  --once            drain the queue, then exit (cron / Actions friendly)
  --poll SECONDS    loop forever, sleeping SECONDS between polls (default 30)
  --fit-local [A,B] engine self-test without Supabase: fit the named
                    galaxies (or all) and print the result JSON

Dataset versions:
  sparc-2024.1  -> the current SPARC Rotmod_LTG release, ingested live.
                   The zip's sha256 is recorded in every result for
                   provenance. Any other dataset_version fails honestly.

Known limitations (v1, by design):
  * A worker that dies mid-fit leaves the row at 'running'; it stays visible
    in the dashboard as such. Requeue by setting it back to 'queued'.
  * One fit at a time per worker process. Run more processes for parallelism;
    the claim protocol makes that safe.
"""

import argparse
import hashlib
import io
import json
import math
import os
import socket
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import zipfile

import numpy as np
from scipy.optimize import least_squares

WORKER_VERSION = "1.1.0"
INGEST_ENGINE_VERSION = "1.0.0"  # ported from QAGKinetics ingest_sparc.py

# ── Canonical model constants (QAGKinetics sweep_v3.py, verbatim) ────────
UPS = 0.5            # Upsilon* (fixed, canonical demo value)
A0 = 1.2e-10         # MOND acceleration scale, m/s^2
KPC_M = 3.085677581e19

DATASETS = {
    "sparc-2024.1": {
        "zip_url_env": "SPARC_ZIP_URL",
        "default_zip_url": "https://astroweb.cwru.edu/SPARC/Rotmod_LTG.zip",
    }
}

# ══════════════════════ SPARC ingestion (port of ingest_sparc.py) ═══════

COLUMNS = ["Rad", "Vobs", "errV", "Vgas", "Vdisk", "Vbul"]
MIN_POINTS = 4


def parse_rotmod(text):
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 6:
            continue
        try:
            vals = [float(x) for x in parts[:6]]
        except ValueError:
            continue
        rows.append(dict(zip(COLUMNS, vals)))
    return rows


def quality_filter(rows):
    kept = []
    for r in rows:
        if (r["errV"] > 0 and r["Vobs"] > 0
                and all(math.isfinite(r[c]) for c in ("Vobs", "errV", "Vgas", "Vdisk", "Vbul"))):
            kept.append(r)
    return kept


def load_dataset(dataset_version, cache_dir):
    """Download (once, cached) + ingest the SPARC release.
    Returns (galaxies, provenance) where galaxies maps name -> arrays dict."""
    spec = DATASETS.get(dataset_version)
    if spec is None:
        raise ValueError(
            f"unknown dataset_version {dataset_version!r}; known: {sorted(DATASETS)}")
    url = os.environ.get(spec["zip_url_env"], spec["default_zip_url"])
    os.makedirs(cache_dir, exist_ok=True)
    zip_path = os.path.join(cache_dir, f"Rotmod_LTG_{dataset_version}.zip")
    if not os.path.exists(zip_path):
        tmp = zip_path + ".part"
        with urllib.request.urlopen(url, timeout=300) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, zip_path)
    blob = open(zip_path, "rb").read()
    zip_sha256 = hashlib.sha256(blob).hexdigest()

    galaxies = {}
    low_dof = set()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = sorted(n for n in z.namelist() if n.endswith("_rotmod.dat"))
        for name in names:
            gname = os.path.basename(name).replace("_rotmod.dat", "")
            rows = quality_filter(parse_rotmod(z.read(name).decode("utf-8", "replace")))
            if len(rows) < MIN_POINTS:
                continue
            a = np.array([[r["Rad"], r["Vobs"], r["errV"], r["Vgas"], r["Vdisk"], r["Vbul"]]
                          for r in rows])
            galaxies[gname] = dict(r=a[:, 0], vobs=a[:, 1], err=a[:, 2],
                                   vgas=a[:, 3], vdisk=a[:, 4], vbul=a[:, 5])
            if len(rows) - 2 == 2:
                low_dof.add(gname)
    if not galaxies:
        raise ValueError("ingestion produced zero usable galaxies")
    provenance = {
        "source_url": url,
        "zip_sha256": zip_sha256,
        "ingest_engine": INGEST_ENGINE_VERSION,
        "n_galaxies": len(galaxies),
        "n_points": int(sum(len(d["r"]) for d in galaxies.values())),
        "low_dof_galaxies": sorted(low_dof),
    }
    return galaxies, provenance


# ══════════════════════ Fit engine (port of sweep_v3.py) ════════════════

def v_baryon_sq(d):
    return UPS * (d["vdisk"] ** 2 + d["vbul"] ** 2) + d["vgas"] ** 2


def v_qag(r, d, v_inf, r_aff):
    return np.sqrt(np.maximum(0.0, v_baryon_sq(d) + v_inf ** 2 * (1.0 - np.exp(-r / r_aff))))


def v_newton(d):
    return np.sqrt(np.maximum(0.0, v_baryon_sq(d)))


def v_mond(r, d):
    vb_si = np.sqrt(np.maximum(0.0, v_baryon_sq(d))) * 1000.0
    r_si = r * KPC_M
    with np.errstate(divide="ignore", invalid="ignore"):
        y = np.where(vb_si > 0, vb_si ** 2 / (r_si * A0), np.inf)
        nu = 0.5 + np.sqrt(0.25 + 1.0 / y)
        v2 = np.where(vb_si > 0, vb_si ** 2 * nu, 0.0)
    return np.sqrt(v2) / 1000.0


def fit_one(d):
    r, vobs, err = d["r"], d["vobs"], d["err"]

    def resid(p):
        return (vobs - v_qag(r, d, p[0], p[1])) / err

    sol = least_squares(resid, x0=[120.0, 3.0], bounds=([0.0, 0.1], [400.0, 60.0]),
                        xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=500)
    v_inf, r_aff = float(sol.x[0]), float(sol.x[1])
    pred = v_qag(r, d, v_inf, r_aff)
    chi2 = float(np.sum(((vobs - pred) / err) ** 2))
    rms = float(np.sqrt(np.mean((vobs - pred) ** 2)))
    pinned = bool(v_inf <= 0.01 or v_inf >= 399.9 or r_aff <= 0.11 or r_aff >= 59.9)
    return v_inf, r_aff, chi2, rms, pinned


def _glob(c, dd, p):
    red = c / dd
    return dict(chi2=round(c, 4), dof=dd, red=round(red, 6),
                sigma=round(float(np.sqrt(2 / dd)), 4),
                z=round(float((red - 1) / np.sqrt(2 / dd)), 2),
                bic=round(float(c + p * np.log(dd) + p), 2))


def run_fit(galaxies, provenance, params):
    params = params or {}
    wanted = params.get("galaxies")
    if wanted:
        unknown = [g for g in wanted if g not in galaxies]
        if unknown:
            raise ValueError(f"unknown galaxies in params: {unknown}")
        names = sorted(wanted)
    else:
        names = sorted(galaxies)
    low_dof = set(provenance["low_dof_galaxies"])

    rows = []
    tot = dict(qag_c=0.0, qag_d=0, newt_c=0.0, newt_d=0, mond_c=0.0, mond_d=0)
    t0 = time.time()
    for name in names:
        d = galaxies[name]
        n = len(d["r"])
        v_inf, r_aff, chi2_q, rms_q, pinned = fit_one(d)
        chi2_n = float(np.sum(((d["vobs"] - v_newton(d)) / d["err"]) ** 2))
        chi2_m = float(np.sum(((d["vobs"] - v_mond(d["r"], d)) / d["err"]) ** 2))
        rows.append(dict(
            name=name, n=n, low_dof=name in low_dof,
            v_inf=round(v_inf, 2), r_aff=round(r_aff, 3),
            qag_chi2=round(chi2_q, 4), qag_dof=n - 2,
            qag_red=round(chi2_q / (n - 2), 4), qag_rms=round(rms_q, 2),
            newt_chi2=round(chi2_n, 4), newt_red=round(chi2_n / n, 4),
            mond_chi2=round(chi2_m, 4), mond_red=round(chi2_m / n, 4),
            pinned=pinned))
        tot["qag_c"] += chi2_q; tot["qag_d"] += n - 2
        tot["newt_c"] += chi2_n; tot["newt_d"] += n
        tot["mond_c"] += chi2_m; tot["mond_d"] += n
    nq = len(rows)
    return {
        "engine": {"worker": WORKER_VERSION, "models": "sweep_v3 port (QAGKinetics)"},
        "dataset": provenance,
        "upsilon_star": UPS,
        "n_galaxies": nq,
        "runtime_secs": round(time.time() - t0, 2),
        "qag": _glob(tot["qag_c"], tot["qag_d"], 2 * nq),
        "newton": _glob(tot["newt_c"], tot["newt_d"], 0),
        "mond": _glob(tot["mond_c"], tot["mond_d"], 0),
        "qag_median_red_chi2": round(float(np.median([r["qag_red"] for r in rows])), 4),
        "qag_beats_mond": sum(1 for r in rows if r["qag_red"] < r["mond_red"]),
        "qag_beats_newton": sum(1 for r in rows if r["qag_red"] < r["newt_red"]),
        "pinned": [r["name"] for r in rows if r["pinned"]],
        "galaxies": rows,
    }


# ══════════════════════ Supabase REST I/O ═════════════════════════════════

class Supa:
    def __init__(self, url, service_key):
        self.base = url.rstrip("/")
        self.h = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
                  "Content-Type": "application/json"}

    def _req(self, method, path, body=None, extra=None):
        headers = dict(self.h)
        if extra:
            headers.update(extra)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw.decode()[:300]}

    def next_queued(self):
        s, r = self._req("GET", "/rest/v1/fits?status=eq.queued&order=created_at.asc"
                                "&limit=1&select=id,user_id,dataset_version,params,created_at")
        if s == 200 and r:
            return r[0]
        return None

    def claim(self, fit_id):
        s, r = self._req("PATCH", f"/rest/v1/fits?id=eq.{fit_id}&status=eq.queued",
                         {"status": "running"},
                         {"Prefer": "return=representation"})
        return s == 200 and bool(r)

    def finish(self, fit_id, status, result):
        s, r = self._req("PATCH", f"/rest/v1/fits?id=eq.{fit_id}",
                         {"status": status, "result": result},
                         {"Prefer": "return=minimal"})
        if s not in (200, 204):
            raise RuntimeError(f"finish PATCH failed: {s} {r}")

    def grant_researcher(self, email):
        """Owner comps: set tier='researcher' for a profile by email.
        Runs as service_role — the only channel the tier-lock trigger accepts
        (dashboard SQL and Table Editor edits are rejected by design).
        Returns 'granted' | 'already' | 'no-profile'."""
        s, r = self._req("GET", "/rest/v1/profiles?email=eq." +
                         urllib.parse.quote(email, safe="") + "&select=id,tier")
        if s != 200 or not r:
            return "no-profile"
        row = r[0]
        if row["tier"] == "researcher":
            return "already"
        s, r = self._req("PATCH", f"/rest/v1/profiles?id=eq.{row['id']}",
                         {"tier": "researcher"},
                         {"Prefer": "return=representation"})
        if s == 200 and r and r[0].get("tier") == "researcher":
            return "granted"
        raise RuntimeError(f"grant PATCH failed: {s} {r}")


def apply_admin_grants(supa):
    """Emails listed in admin_grants.txt (next to this script) are granted
    the researcher tier at worker startup. Idempotent; the file is the
    owner's comp list — repo write access controls it."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_grants.txt")
    if not os.path.exists(path):
        return
    emails = []
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            emails.append(line)
    for email in emails:
        outcome = supa.grant_researcher(email)
        log(f"admin grant: {email} -> {outcome}")


# ══════════════════════ Main loop ═════════════════════════════════════════

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def process_one(supa, dataset_cache):
    fit = supa.next_queued()
    if not fit:
        return False
    fid = fit["id"]
    if not supa.claim(fid):
        log(f"fit {fid[:8]}: lost the claim race, skipping")
        return True
    log(f"fit {fid[:8]}: claimed (dataset {fit['dataset_version']})")
    try:
        dv = fit["dataset_version"]
        if dv not in dataset_cache:
            dataset_cache[dv] = load_dataset(
                dv, os.environ.get("SPARC_CACHE_DIR",
                                   os.path.expanduser("~/.cache/qag-nexus-worker")))
            log(f"dataset {dv}: ingested {dataset_cache[dv][1]['n_galaxies']} galaxies, "
                f"{dataset_cache[dv][1]['n_points']} points")
        galaxies, provenance = dataset_cache[dv]
        result = run_fit(galaxies, provenance, fit.get("params"))
        supa.finish(fid, "done", result)
        log(f"fit {fid[:8]}: DONE — {result['n_galaxies']} galaxies, "
            f"QAG red-chi2 {result['qag']['red']}, runtime {result['runtime_secs']}s")
    except Exception as e:  # honest failure, recorded on the row
        log(f"fit {fid[:8]}: FAILED — {e}")
        try:
            supa.finish(fid, "failed", {"error": str(e), "worker": WORKER_VERSION})
        except Exception as e2:
            log(f"fit {fid[:8]}: could not record failure: {e2}")
    return True


def main():
    ap = argparse.ArgumentParser(description="QAG Nexus compute worker")
    ap.add_argument("--once", action="store_true", help="drain the queue, then exit")
    ap.add_argument("--poll", type=int, default=0, metavar="SECONDS",
                    help="loop forever, sleeping SECONDS between polls")
    ap.add_argument("--fit-local", metavar="A,B", nargs="?", const="",
                    help="engine self-test without Supabase (named galaxies or all)")
    args = ap.parse_args()

    if args.fit_local is not None:
        names = [g for g in args.fit_local.split(",") if g] or None
        galaxies, provenance = load_dataset(
            "sparc-2024.1",
            os.environ.get("SPARC_CACHE_DIR", os.path.expanduser("~/.cache/qag-nexus-worker")))
        result = run_fit(galaxies, provenance, {"galaxies": names} if names else {})
        print(json.dumps({k: v for k, v in result.items() if k != "galaxies"}, indent=2))
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    supa = Supa(url, key)
    worker_id = os.environ.get("WORKER_ID", socket.gethostname())
    log(f"worker {worker_id} up (v{WORKER_VERSION}) against {url}")
    try:
        apply_admin_grants(supa)
    except Exception as e:
        log(f"admin grant error: {e}")

    dataset_cache = {}
    if args.poll:
        while True:
            try:
                while process_one(supa, dataset_cache):
                    pass
            except Exception as e:
                log(f"poll cycle error: {e}")
            time.sleep(args.poll)
    else:  # --once or default single pass
        while process_one(supa, dataset_cache):
            pass
        log("queue drained")


if __name__ == "__main__":
    main()
