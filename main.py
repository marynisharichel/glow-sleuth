import argparse
from pathlib import Path

from glow_sleuth.agent import GlowSleuth
from glow_sleuth.config import setup_serper_key
from glow_sleuth.live import SerperShopping
from glow_sleuth.llm import polish
from glow_sleuth.sources import JsonCatalogSource

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def main():
    ap = argparse.ArgumentParser(description="Glow Sleuth - explainable beauty product recommendations")
    ap.add_argument("query", nargs="?", help='e.g. "serum for dry skin under 1000"')
    ap.add_argument("--top", type=int, default=0, help="limit results (default: show ALL matches)")
    ap.add_argument("--llm", action="store_true", help="polish output with Claude (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--key", help="Serper API key (otherwise read from .env or asked)")
    args = ap.parse_args()
    setup_serper_key(ROOT, "", args.key)

    query = args.query or input("Tell me your skin type, product and budget: ")
    prefs, trace, recs = GlowSleuth([JsonCatalogSource(DATA)], live=SerperShopping()).run(query, args.top or None)

    print("\n--- Agent trace ---")
    print("\n".join(trace))
    if not recs:
        print("\nNo products match. Try raising your budget or removing a filter.")
        return
    print(f"\n--- {len(recs)} recommendation(s), best reviews first ---")
    block = "\n\n".join(r.explanation for r in recs)
    print(polish(query, block) if args.llm else block)


if __name__ == "__main__":
    main()
