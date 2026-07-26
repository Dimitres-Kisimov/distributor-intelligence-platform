"""``python -m dip`` — boot the Flask app, or emit executive deliverables.

Usage
-----
``python -m dip``                 run the dashboard on http://localhost:5000
``python -m dip --deliverables``  write deliverables/*.pdf and *.xlsx
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
    from dip.prescribe import build_plan

    plan = build_plan(build_dataset())
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, int]] = []
    jobs = [
        ("executive_review.pdf", exports.build_pdf),
        ("executive_workbook.xlsx", exports.build_excel),
    ]
    for name, builder in jobs:
        data = builder(plan=plan)
        path = out_dir / name
        path.write_bytes(data)
        size = path.stat().st_size
        written.append((path, size))
        print(f"[OK] wrote {path}  ({size / 1024:.1f} KB)")
        if size < 10_240:
            print(f"[WARN] {path} is smaller than 10 KB", file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dip", description=__doc__)
    parser.add_argument(
        "--deliverables",
        action="store_true",
        help="write the executive PDF + Excel deliverables and exit",
    )
    parser.add_argument(
        "--out",
        default="deliverables",
        help="output directory for --deliverables (default: deliverables)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Flask host (server mode)")
    parser.add_argument("--port", type=int, default=5000, help="Flask port (server mode)")
    args = parser.parse_args(argv)

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
