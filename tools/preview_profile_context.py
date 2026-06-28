from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.profile_analyzer.context import (
    format_profile_context,
    load_profile,
    select_profile_context,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview learned profile context for a question.")
    parser.add_argument("profile_path")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    profile = load_profile(args.profile_path)
    context = select_profile_context(args.question, profile, top_k=args.top_k)
    print(format_profile_context(context))


if __name__ == "__main__":
    main()
