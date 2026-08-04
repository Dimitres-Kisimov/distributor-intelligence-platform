"""``python -m dip`` — boot the Flask app, or emit executive deliverables.

Usage
-----
``python -m dip``                 run the dashboard on http://localhost:5000
``python -m dip --deliverables``  write deliverables/*.pdf and *.xlsx
``python -m dip --reconcile``     write docs/RECONCILIATION.md + reconciliation.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows console safety: emit UTF-8 and never crash on an odd glyph.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - very old interpreters
    pass


def _write_deliverables(out_dir: Path) -> list[tuple[Path, int]]:
    """Render the PDF and Excel deliverables into ``out_dir``.

    The plan (and its routing solve) is built exactly once and shared by both
    deliverables, so the PDF, the workbook and a running dashboard on the same
    machine all quote identical numbers.
    """
    from dip import exports
    from dip.data import build_dataset
    from dip.explain import explain_plan
    from dip.optimize import optimize_prices, optimize_routes
    from dip.prescribe import build_plan

    ds = build_dataset()
    routes = optimize_routes(ds)
    prices = optimize_prices(ds)
    plan = build_plan(ds, routes=routes, prices=prices)
    explanation = explain_plan(ds, plan=plan, routes=routes, prices=prices)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, int]] = []
    jobs = [
        ("executive_review.pdf", exports.build_pdf),
        ("executive_workbook.xlsx", exports.build_excel),
    ]
    for name, builder in jobs:
        data = builder(plan=plan, ds=ds, explanation=explanation)
        path = out_dir / name
        path.write_bytes(data)
        size = path.stat().st_size
        written.append((path, size))
        print(f"[OK] wrote {path}  ({size / 1024:.1f} KB)")
        if size < 10_240:
            print(f"[WARN] {path} is smaller than 10 KB", file=sys.stderr)
    return written


def _write_reconciliation() -> int:
    """Write the reconciliation report to docs/ and report the check result.

    Regenerates docs/RECONCILIATION.md + docs/reconciliation.csv (both committed,
    tracked deliverables) and returns 0 only if every cross-engine identity holds
    and every headline number is still present in the README.
    """
    from dip import reconcile as rec_mod

    rec = rec_mod.reconcile()
    paths = rec_mod.write_reports()
    for name, path in paths.items():
        print(f"[OK] wrote {path}  ({name})")
    n_ok = sum(1 for i in rec["identities"] if i["ok"])
    print(f"[{'OK' if rec['identities_ok'] else 'FAIL'}] identities: {n_ok}/{len(rec['identities'])} hold")
    if rec["readme_ok"]:
        print(f"[OK] README presence: all {len(rec['claims'])} headline numbers found")
    else:
        print(f"[FAIL] README missing: {rec['readme_missing']}", file=sys.stderr)
    print("[OK] reconciliation clean" if rec["all_ok"] else "[FAIL] reconciliation found drift")
    return 0 if rec["all_ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dip", description=__doc__)
    parser.add_argument(
        "--deliverables",
        action="store_true",
        help="write the executive PDF + Excel deliverables and exit",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="write docs/RECONCILIATION.md + reconciliation.csv and exit",
    )
    parser.add_argument(
        "--out",
        default="deliverables",
        help="output directory for --deliverables (default: deliverables)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Flask host (server mode)")
    parser.add_argument("--port", type=int, default=5000, help="Flask port (server mode)")
    args = parser.parse_args(argv)

    if args.reconcile:
        return _write_reconciliation()

    if args.deliverables:
        written = _write_deliverables(Path(args.out))
        ok = all(size >= 10_240 for _, size in written)
        print("[OK] deliverables complete" if ok else "[WARN] a deliverable was undersized")
        return 0 if ok else 1

    from app import app

    print(f"[OK] starting dashboard on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
