from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.contracts import ContractError, write_json
from pipeline.local import build_local_readiness
from pipeline.rules import build_manifest, validate_rule_package
from pipeline.stages import validate_run


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = SKILL_ROOT / "references" / "rules"
DEFAULT_MODULES = SKILL_ROOT / "references" / "samo-project-modules.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="qa-case-xlsx 独立本地确定性流水线门禁")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rules = subparsers.add_parser("validate-rules")
    rules.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES)
    rules.add_argument("--staged", action="store_true")

    manifest = subparsers.add_parser("build-manifest")
    manifest.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES)
    manifest.add_argument("--release-version", required=True)
    manifest.add_argument("--staged", action="store_true")

    run = subparsers.add_parser("validate-run")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--modules", type=Path, default=DEFAULT_MODULES)
    run.add_argument("--out", type=Path)

    readiness = subparsers.add_parser("readiness-local")
    readiness.add_argument("--run-dir", type=Path, required=True)
    readiness.add_argument("--modules", type=Path, default=DEFAULT_MODULES)
    readiness.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "validate-rules":
            result = validate_rule_package(args.rules_dir, staged=args.staged)
        elif args.command == "build-manifest":
            result = build_manifest(args.rules_dir, args.release_version, staged=args.staged)
        elif args.command == "validate-run":
            result = validate_run(args.run_dir, args.modules)
            if args.out:
                write_json(args.out, result)
        else:
            result = build_local_readiness(args.run_dir, args.modules)
            out = args.out or args.run_dir / "delivery_readiness.json"
            write_json(out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") != "ok" and args.command != "build-manifest":
            raise SystemExit(1)
    except ContractError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
