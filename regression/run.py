"""
Runs the corpus at a deployed box and scores what came back.

Scores the box's own `reference`, not a regex over its text, so this measures
the contract a caller actually depends on.

    OCR_SECRET=... OCR_URL=https://box.example uv run --group dev python regression/run.py [concurrency]

Add `sweep` to measure throughput against concurrency instead:

    OCR_SECRET=... uv run --group dev python regression/run.py sweep
"""
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus")
BASE = os.environ.get("OCR_URL", "http://127.0.0.1:8765").rstrip("/")
SECRET = os.environ.get("OCR_SECRET", "")


def load():
    path = os.path.join(HERE, "manifest.json")
    if not os.path.exists(path):
        sys.exit("no manifest; run regression/generate.py first")
    return json.load(open(path))


def call(entry):
    started = time.perf_counter()
    try:
        with open(os.path.join(CORPUS, entry["file"]), "rb") as fh:
            r = requests.post(
                f"{BASE}/ocr",
                headers={"x-ocr-secret": SECRET},
                files={"file": (entry["file"], fh, "image/jpeg")},
                data={"provider": entry["provider"]},
                timeout=180,
            )
        wall = (time.perf_counter() - started) * 1000
        if r.status_code != 200:
            return {**entry, "ok": False, "status": r.status_code, "wall": wall}
        body = r.json()
        return {**entry, "ok": True, "status": 200, "wall": wall, "ms": body.get("ms"),
                "engine": body.get("engine"), "got": body.get("reference"),
                "hit": body.get("reference") == entry["expect"]}
    except Exception as exc:
        return {**entry, "ok": False, "status": 0, "wall": (time.perf_counter() - started) * 1000,
                "err": f"{type(exc).__name__}: {exc}"[:120]}


def pct(values, p):
    if not values:
        return 0
    values = sorted(values)
    return values[max(0, min(len(values) - 1, int(round(p / 100 * len(values))) - 1))]


def group(label, rows):
    if not rows:
        return
    hits = sum(1 for r in rows if r["hit"])
    ms = [r["ms"] for r in rows if r.get("ms") is not None]
    print(f"  {label:<24} {hits:>4}/{len(rows):<4} {100 * hits / len(rows):6.1f}%   p50={pct(ms, 50):>5}ms p95={pct(ms, 95):>6}ms")


def accuracy(manifest, workers):
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(call, manifest))
    failed = [r for r in res if not r["ok"]]
    ok = [r for r in res if r["ok"]]
    print(f"=== {len(res)} images at concurrency {workers} in {time.time() - started:.0f}s, "
          f"{len(failed)} non-200 ===")
    group("all", ok)
    print("\n--- by tier ---")
    for tier in ("clean", "degraded"):
        group(tier, [r for r in ok if r["tier"] == tier])
    print("\n--- by kind ---")
    for kind in sorted({r["kind"] for r in ok}):
        group(kind, [r for r in ok if r["kind"] == kind])
    print("\n--- by engine that answered ---")
    for engine in sorted({r.get("engine") or "?" for r in ok}):
        group(engine, [r for r in ok if r.get("engine") == engine])
    print("\n--- images carrying a QR ---")
    group("has_qr", [r for r in ok if r["has_qr"]])
    group("no qr", [r for r in ok if not r["has_qr"]])
    ms = [r["ms"] for r in ok if r.get("ms") is not None]
    wall = [r["wall"] for r in ok]
    print(f"\n--- latency ---\n  server p50={pct(ms, 50)} p95={pct(ms, 95)} p99={pct(ms, 99)} max={max(ms) if ms else 0}")
    print(f"  wall   p50={pct(wall, 50):.0f} p95={pct(wall, 95):.0f}")
    wrong = [r for r in ok if not r["hit"] and r["got"]]
    blank = [r for r in ok if not r["hit"] and not r["got"]]
    print(f"\n--- misses ---\n  answered wrong {len(wrong)}   answered nothing {len(blank)}")
    for r in wrong[:8]:
        print(f"    {r['file']:<22} want={str(r['expect'])[:34]:<34} got={str(r['got'])[:34]}")
    if failed:
        print("\n--- non-200 ---", dict(Counter(r["status"] for r in failed)))
    json.dump(res, open(os.path.join(HERE, "results.json"), "w"))
    print(f"\nwrote {HERE}/results.json")


def sweep(manifest):
    subset = manifest[:48]
    print(f"{'conc':>5} {'wall_s':>8} {'rps':>7} {'p50':>7} {'p95':>7} {'errors':>7}")
    for c in (1, 2, 4, 8, 16):
        started = time.time()
        with ThreadPoolExecutor(max_workers=c) as ex:
            res = list(ex.map(call, subset))
        elapsed = time.time() - started
        ok = [r for r in res if r["ok"]]
        walls = [r["wall"] for r in ok]
        print(f"{c:>5} {elapsed:>8.1f} {len(res) / elapsed:>7.2f} "
              f"{pct(walls, 50):>7.0f} {pct(walls, 95):>7.0f} {len(res) - len(ok):>7}")


if __name__ == "__main__":
    data = load()
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        sweep(data)
    else:
        accuracy(data, int(sys.argv[1]) if len(sys.argv) > 1 else 4)
