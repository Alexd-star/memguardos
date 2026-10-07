#!/usr/bin/env python3
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.integrations.longmemeval_v2 import evaluate_questions, to_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 LongMemEval-V2 外部检索交叉验证")
    parser.add_argument("--trajectories", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--context-limit", type=int, default=8)
    args = parser.parse_args()
    result = evaluate_questions(
        Path(args.trajectories),
        Path(args.questions),
        Path(args.work_dir),
        limit=args.limit,
        context_limit=args.context_limit,
    )
    output = Path(args.output)
    markdown = Path(args.markdown)
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown.write_text(to_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
