"""Lab 3 CLI entrypoint for the LangGraph Tang-poem agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterator

from common.poem_utils import write_json
from lab3.poetry_skill import PoemRequest, PoetryLangGraphAgent


class PoemReActAgent(PoetryLangGraphAgent):
    """Backward-compatible alias for older lab code.

    The implementation is now LangGraph-based; this name is kept so existing
    Streamlit/CLI imports do not break during the migration.
    """

    def run_iter(self, request: PoemRequest) -> Iterator[Dict[str, Any]]:
        yield from self.stream(request)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", default="春天")
    parser.add_argument("--genre", default="七言绝句", choices=["七言绝句", "五言绝句"])
    parser.add_argument("--emotion", default="清新")
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--out", default="outputs/lab3_case_logs.json")
    args = parser.parse_args()

    agent = PoetryLangGraphAgent(max_steps=args.max_steps)
    result = agent.run(PoemRequest(theme=args.theme, genre=args.genre, emotion=args.emotion))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
