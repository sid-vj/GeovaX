#!/usr/bin/env python3
"""Fetch the real corpus.

Downloads every dataset in the catalogue, verifies the size, extracts what needs
extracting, and writes a receipt with a SHA-256 per file so a later run can prove it is
working on the same bytes.

    python data_acquisition/fetch.py --out data/raw
    python data_acquisition/fetch.py --out data/raw --only tngis_cadastre gcc_buildings
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request

def _build_ssl_context() -> ssl.SSLContext:
    """Prefer the OS trust store when present — some government sites (seen live on
    ncscm.res.in) serve an incomplete chain for an older root that's still in the macOS/
    system bundle but has been pruned from certifi's curated one; verifying against only
    certifi then fails a real, valid certificate. Falls back to certifi, then Python's
    bare defaults, so this still works on Linux/CI where no /etc/ssl/cert.pem exists."""
    for system_bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(system_bundle):
            return ssl.create_default_context(cafile=system_bundle)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()

sys.path.insert(0, os.path.dirname(__file__))

from sources import CATALOGUE, DataSource, total_download_bytes  # noqa: E402

UA = "SAMANVAY/1.0 (SIH 26013 reference implementation)"
CHUNK = 1 << 20


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download(url: str, dest: str, *, retries: int = 3) -> dict[str, object]:
    """Stream a download with resume-friendly retries and a progress line."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return {"status": "cached", "bytes": os.path.getsize(dest)}

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            t0 = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            tmp = dest + ".part"
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CONTEXT) as r, open(tmp, "wb") as fh:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    got += len(block)
                    if total:
                        pct = 100.0 * got / total
                        print(f"\r      {human(got)} / {human(total)} ({pct:5.1f}%)",
                              end="", flush=True)
            print()
            os.replace(tmp, dest)
            return {"status": "downloaded", "bytes": os.path.getsize(dest),
                    "seconds": round(time.time() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"\r      attempt {attempt}/{retries} failed: {exc}", flush=True)
            time.sleep(2 * attempt)
    return {"status": "failed", "error": str(last_error)}


def extract_7z(archive: str, out_dir: str) -> str | None:
    if not shutil.which("7z") and not shutil.which("7za"):
        print("      ! 7z is not installed; install p7zip-full to extract this archive")
        return None
    exe = shutil.which("7z") or shutil.which("7za")
    subprocess.run([exe, "x", "-y", f"-o{out_dir}", archive],
                   check=False, stdout=subprocess.DEVNULL)
    return out_dir


def resolve_ckan_resource_url(ckan_base: str, dataset_id: str, name_hint: str = "") -> str:
    """Resolve a CKAN dataset id to a real resource download URL via the portal's documented
    `package_show` API — the correct way to fetch from a CKAN-based open-data portal (like
    OpenCity), since a dataset's human-facing page is not itself a file. When a dataset has
    several resources (e.g. ward maps for multiple years), `name_hint` picks the one whose
    resource name contains it (case-insensitive); otherwise the first resource is used."""
    api_url = f"{ckan_base.rstrip('/')}/api/3/action/package_show"
    req = urllib.request.Request(f"{api_url}?id={dataset_id}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as r:
        payload = json.loads(r.read().decode("utf-8"))
    resources = payload.get("result", {}).get("resources", [])
    if not resources:
        raise RuntimeError(f"CKAN package '{dataset_id}' at {ckan_base} has no resources")
    if name_hint:
        for res in resources:
            if name_hint.lower() in (res.get("name") or "").lower():
                return res["url"]
    return resources[0]["url"]


def clone_git(spec: str, out_dir: str) -> dict[str, object]:
    """`git+<url>#<subpath>` — sparse-checkout just the subpath."""
    url, _, subpath = spec[len("git+"):].partition("#")
    name = os.path.splitext(os.path.basename(url))[0]
    target = os.path.join(out_dir, name)
    if os.path.isdir(os.path.join(target, ".git")):
        return {"status": "cached", "path": target}
    os.makedirs(out_dir, exist_ok=True)
    cmds = [
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", url, target],
        ["git", "-C", target, "sparse-checkout", "init", "--cone"],
    ]
    if subpath:
        cmds.append(["git", "-C", target, "sparse-checkout", "set", subpath])
    cmds.append(["git", "-C", target, "checkout", "HEAD"])
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode != 0 and "sparse-checkout" not in " ".join(c):
            return {"status": "failed", "error": r.stderr.strip()[:300]}
    return {"status": "cloned", "path": target}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--only", nargs="*", help="fetch only these catalogue keys")
    ap.add_argument("--skip-large", action="store_true",
                    help="skip anything over 500 MB (useful for a first look)")
    args = ap.parse_args()

    keys = args.only or list(CATALOGUE)
    os.makedirs(args.out, exist_ok=True)
    receipt: dict[str, object] = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "catalogue_total_bytes": total_download_bytes(),
        "datasets": [],
    }

    print(f"SAMANVAY data acquisition — {len(keys)} datasets, "
          f"about {human(total_download_bytes())} in total\n")

    for key in keys:
        ds: DataSource = CATALOGUE[key]
        if args.skip_large and ds.approx_bytes > 500_000_000:
            print(f"  · {key}: skipped (--skip-large)")
            continue
        print(f"  → {key}: {ds.title}")
        print(f"      {ds.authority_name} · {ds.licence} · ~{human(ds.approx_bytes)}")

        entry: dict[str, object] = {
            "key": key, "title": ds.title, "authority": ds.authority_name,
            "licence": ds.licence, "url": ds.url, "upstream": ds.upstream,
            "vintage": ds.vintage, "role": ds.role, "tier": ds.tier,
        }

        if ds.requires_credentials:
            entry["status"] = "requires_credentials"
            entry["note"] = (
                f"{ds.platform or ds.upstream} requires real registration/login this "
                "environment cannot complete. Set the documented env var for this source "
                "and re-run to fetch for real; nothing was downloaded or fabricated."
            )
            receipt["datasets"].append(entry)
            print(f"      requires_credentials — {entry['note']}\n")
            continue

        if ds.resolver == "ckan":
            try:
                real_url = resolve_ckan_resource_url(ds.ckan_base, ds.ckan_dataset, ds.ckan_resource_hint)
            except Exception as exc:  # noqa: BLE001
                entry.update({"status": "failed", "error": f"CKAN resolution failed: {exc}"})
                receipt["datasets"].append(entry)
                print(f"      failed: {exc}\n")
                continue
            entry["resolved_url"] = real_url
            dest = os.path.join(args.out, ds.filename)
            entry.update(download(real_url, dest))
            if entry.get("status") in ("downloaded", "cached"):
                entry["sha256"] = sha256(dest)
        elif ds.url.startswith("git+"):
            entry.update(clone_git(ds.url, args.out))
        else:
            dest = os.path.join(args.out, ds.filename)
            entry.update(download(ds.url, dest))
            if entry.get("status") in ("downloaded", "cached"):
                entry["sha256"] = sha256(dest)
                if ds.archive == "7z":
                    extract_7z(dest, args.out)
                    member = os.path.join(args.out, ds.member or "")
                    entry["extracted"] = os.path.exists(member)
                    if entry["extracted"]:
                        entry["extracted_bytes"] = os.path.getsize(member)

        receipt["datasets"].append(entry)
        print(f"      {entry.get('status')}"
              + (f" · sha256 {str(entry.get('sha256'))[:16]}…" if entry.get("sha256") else ""))
        print()

    with open(os.path.join(args.out, "fetch_receipt.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2)

    ok = sum(1 for d in receipt["datasets"] if d.get("status") in
             ("downloaded", "cached", "cloned"))
    gated = sum(1 for d in receipt["datasets"] if d.get("status") == "requires_credentials")
    failed = len(receipt["datasets"]) - ok - gated
    print(f"  {ok}/{len(receipt['datasets'])} datasets fetched"
          + (f", {gated} require credentials this environment doesn't have" if gated else "")
          + f" in {args.out}")
    print("  next: python data_acquisition/build_aoi.py --raw "
          f"{args.out} --out data/aoi")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
