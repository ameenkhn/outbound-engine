"""Targeting CLI — the human sign-off + dry-run entry points (L1 / M1).

    python -m targeting approve <spec_id>     # flip approved=TRUE (sign-off)
    python -m targeting expand kw1 kw2 ...     # Mode B: write an approved spec
    python -m targeting deep "<persona text>"  # Mode A: write an UNapproved spec
    python -m targeting show <spec_id>         # print a spec

``approve`` is the load-bearing one: it is how a Mode A (deep) spec becomes
sourceable. The real model is only used by ``expand``/``deep`` and needs
``ANTHROPIC_API_KEY``; ``approve``/``show`` are pure DB and need only
``DATABASE_URL``.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .brain import approve, load_spec, run_mode_a, run_mode_b


def _connect():
    from data.db import connect

    return connect()


def _cmd_approve(args) -> int:
    conn = _connect()
    try:
        ok = approve(conn, args.spec_id)
    finally:
        conn.close()
    if ok:
        print("approved target_spec id={0}".format(args.spec_id))
        return 0
    print("no target_spec with id={0}".format(args.spec_id), file=sys.stderr)
    return 1


def _cmd_expand(args) -> int:
    from .brain import AnthropicBrain, FakeBrain

    brain = FakeBrain() if args.fake else AnthropicBrain()
    conn = _connect()
    try:
        spec = run_mode_b(conn, args.keywords, brain=brain)
    finally:
        conn.close()
    print("wrote approved keyword spec id={0}: {1} keywords".format(
        spec.id, len(spec.expanded_keywords)))
    return 0


def _cmd_deep(args) -> int:
    from .brain import AnthropicBrain, FakeBrain

    brain = FakeBrain() if args.fake else AnthropicBrain()
    conn = _connect()
    try:
        spec = run_mode_a(conn, args.persona, brain=brain)
    finally:
        conn.close()
    print("wrote UNAPPROVED deep spec id={0}. Approve with: "
          "python -m targeting approve {0}".format(spec.id))
    return 0


def _cmd_show(args) -> int:
    conn = _connect()
    try:
        spec = load_spec(conn, args.spec_id)
    finally:
        conn.close()
    if spec is None:
        print("no target_spec with id={0}".format(args.spec_id), file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "id": spec.id,
            "mode": spec.mode,
            "approved": spec.approved,
            "seed_keywords": spec.seed_keywords,
            "expanded_keywords": spec.expanded_keywords,
            "filters": spec.filters,
            "created_by_model": spec.created_by_model,
        },
        indent=2,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="targeting", description="AI Targeting brain CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("approve", help="flip approved=TRUE for a target_spec (human sign-off)")
    pa.add_argument("spec_id", type=int)
    pa.set_defaults(func=_cmd_approve)

    pe = sub.add_parser("expand", help="Mode B: expand seed keywords -> approved spec")
    pe.add_argument("keywords", nargs="+")
    pe.add_argument("--fake", action="store_true", help="use the offline FakeBrain")
    pe.set_defaults(func=_cmd_expand)

    pd = sub.add_parser("deep", help="Mode A: persona -> UNapproved deep spec")
    pd.add_argument("persona")
    pd.add_argument("--fake", action="store_true", help="use the offline FakeBrain")
    pd.set_defaults(func=_cmd_deep)

    ps = sub.add_parser("show", help="print a target_spec")
    ps.add_argument("spec_id", type=int)
    ps.set_defaults(func=_cmd_show)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
