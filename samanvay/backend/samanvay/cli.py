"""Command-line entry point.

    samanvay run       --aoi mid --out out/chennai
    samanvay serve     --out out/chennai_metro
    samanvay verify    --out out/chennai_metro
    samanvay describe  EPSG:24383
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _run(args: argparse.Namespace) -> int:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.argv = ["run_pipeline.py", "--aoi", args.aoi, "--out", args.out, "--data", args.data]
    script = os.path.join(here, "scripts", "run_pipeline.py")
    exec(compile(open(script).read(), script, "exec"), {"__name__": "__main__"})
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(args.out), host=args.host, port=args.port)
    return 0


def _verify(args: argparse.Namespace) -> int:
    from .core.ledger import ProvenanceLedger

    path = os.path.join(args.out, "ledger.jsonl")
    if not os.path.exists(path):
        print(f"no ledger at {path}")
        return 1
    led = ProvenanceLedger(path)
    ok, broken, msg = led.verify()
    print(json.dumps({
        "entries": len(led), "verified": ok, "broken_at": broken,
        "message": msg, "merkle_root": led.merkle_root(),
    }, indent=2))
    return 0 if ok else 2


def _describe(args: argparse.Namespace) -> int:
    from .crs.engine import CrsEngine

    print(json.dumps(CrsEngine().describe(args.crs), indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="samanvay", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run the harmonisation pipeline")
    p.add_argument("--aoi", default="mid")
    p.add_argument("--data", default="data/aoi")
    p.add_argument("--out", default="out/chennai")
    p.set_defaults(fn=_run)

    p = sub.add_parser("serve", help="serve the OGC API and the console")
    p.add_argument("--out", default="out/chennai_metro")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=_serve)

    p = sub.add_parser("verify", help="verify the provenance ledger")
    p.add_argument("--out", default="out/chennai_metro")
    p.set_defaults(fn=_verify)

    p = sub.add_parser("describe", help="describe a coordinate reference system")
    p.add_argument("crs")
    p.set_defaults(fn=_describe)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
