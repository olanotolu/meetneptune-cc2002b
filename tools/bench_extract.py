"""Benchmark page-2 extraction: pikepdf vs pymupdf, many runs, real numbers."""
from __future__ import annotations

import statistics
import time
from pathlib import Path

import pikepdf
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
PACKET = ROOT / "00_packet" / "neptune-takehome-form-fill-packet (7).pdf"
RUNS = 50


def extract_pikepdf(out: Path) -> None:
    with pikepdf.open(PACKET) as src:
        dst = pikepdf.Pdf.new()
        dst.pages.append(src.pages[1])  # page 2 (0-indexed)
        dst.save(out, deterministic_id=True)


def extract_pymupdf(out: Path) -> None:
    doc = pymupdf.open(str(PACKET))
    try:
        doc.select([1])  # keep only page 2
        doc.save(str(out))
    finally:
        doc.close()


def bench(fn, out: Path, runs: int) -> list[float]:
    times = []
    for _ in range(runs):
        out.unlink(missing_ok=True)
        t0 = time.perf_counter()
        fn(out)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)  # ms
    return times


def main() -> int:
    tmp = ROOT / "outputs" / "_bench"
    tmp.mkdir(parents=True, exist_ok=True)

    # warmup
    extract_pikepdf(tmp / "warm_pike.pdf")
    extract_pymupdf(tmp / "warm_mupdf.pdf")

    pike_times = bench(extract_pikepdf, tmp / "pike.pdf", RUNS)
    mupdf_times = bench(extract_pymupdf, tmp / "mupdf.pdf", RUNS)

    def stats(t):
        return {
            "min": min(t),
            "median": statistics.median(t),
            "mean": statistics.mean(t),
            "max": max(t),
            "stdev": statistics.stdev(t),
        }

    ps, ms = stats(pike_times), stats(mupdf_times)
    print(f"runs: {RUNS}")
    print(f"pikepdf  median={ps['median']:.2f}ms  mean={ps['mean']:.2f}ms  min={ps['min']:.2f}ms  max={ps['max']:.2f}ms  stdev={ps['stdev']:.2f}")
    print(f"pymupdf  median={ms['median']:.2f}ms  mean={ms['mean']:.2f}ms  min={ms['min']:.2f}ms  max={ms['max']:.2f}ms  stdev={ms['stdev']:.2f}")

    # also: byte determinism of the extraction itself
    import hashlib
    pike_hashes = set()
    mupdf_hashes = set()
    for i in range(5):
        extract_pikepdf(tmp / f"p_{i}.pdf")
        pike_hashes.add(hashlib.sha256((tmp / f"p_{i}.pdf").read_bytes()).hexdigest())
        extract_pymupdf(tmp / f"m_{i}.pdf")
        mupdf_hashes.add(hashlib.sha256((tmp / f"m_{i}.pdf").read_bytes()).hexdigest())
    print(f"pikepdf unique hashes over 5 runs: {len(pike_hashes)}")
    print(f"pymupdf unique hashes over 5 runs: {len(mupdf_hashes)}")

    # cleanup
    for f in tmp.glob("*.pdf"):
        f.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
