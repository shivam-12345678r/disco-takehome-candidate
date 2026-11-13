"""Score every sample advertiser brief."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from campaign.catalog import load_example_lines
from campaign.pipeline import draft_campaign

EXPECTATIONS = [
    {
        "match": "senior dogs",
        "status": "ready",
        "top": "Pawline",
        "include": ["Pawline", "Ruffco"],
        "exclude": ["Tailcrate", "Swiftcart", "Velvetline", "Pantrygood"],
    },
    {
        "match": "activewear",
        "status": "ready",
        "include": ["Movewell", "Cloudfoot"],
        "exclude": ["Stride & Stem", "Studiogrid", "Linden Park", "Everbody"],
    },
    {
        "match": "adaptogens",
        "status": "ready",
        "include": ["Pop & Sip"],
        "exclude": ["Swiftcart", "Pantrygood", "Daily Form"],
    },
    {"match": "candles", "status": "no_placement"},
    {"match": "feel better", "status": "needs_clarification"},
    {"match": "backcountry", "status": "no_placement"},
    {"match": "dental", "status": "off_catalog"},
    {"match": "for moms", "status": "needs_clarification"},
    {
        "match": "cleaning",
        "status": "ready",
        "include": ["Hearthstone Goods", "Pantrygood"],
        "exclude": ["Swiftcart", "Kitchenly"],
    },
    {"match": "handbags", "status": "no_placement"},
    {
        "match": "protein bars",
        "status": "ready",
        "include": ["Pantrygood"],
        "exclude": ["Kitchenly", "Swiftcart", "Pop & Sip"],
    },
    {
        "match": "new cat owners",
        "status": "ready",
        "include": ["Tailcrate", "Pawline", "Ruffco"],
        "exclude": ["Velvetline", "Swiftcart"],
    },
    {
        "match": "creatine",
        "status": "ready",
        "include": ["Daily Form"],
        "exclude": ["Movewell", "Studiogrid", "Linden Park"],
    },
    {
        "match": "Linen",
        "status": "ready",
        "include": ["Northbed"],
        "exclude": ["Hearthstone Goods", "Marlowe & Co."],
    },
    {"match": "idk", "status": "needs_clarification"},
]


def main() -> int:
    examples = load_example_lines()
    failed = 0
    for index, expectation in enumerate(EXPECTATIONS):
        description = examples[index] if index < len(examples) else None
        if not description:
            failed += 1
            print(f"MISSING EXAMPLE: {expectation['match']}")
            continue
        draft = draft_campaign(description)
        recommended = [row.publisher.name for row in draft.publishers if row.decision == "recommend"]
        personas = [row.persona.name for row in draft.personas if row.selected]
        headlines = [creative.headline for creative in draft.creatives]
        problems: list[str] = []
        if draft.status != expectation["status"]:
            problems.append(f"status {draft.status} != {expectation['status']}")
        if expectation.get("top") and (not recommended or recommended[0] != expectation["top"]):
            problems.append(f"top {recommended[0] if recommended else None} != {expectation['top']}")
        for name in expectation.get("include", []):
            if name not in recommended:
                problems.append(f"missing {name}")
        for name in expectation.get("exclude", []):
            if name in recommended:
                problems.append(f"should have excluded {name}")
        if draft.status == "ready":
            if not (3 <= len(personas) <= 5):
                problems.append(f"persona count {len(personas)}")
            if len(set(headlines)) != len(headlines):
                problems.append("duplicate headlines")
            if not draft.config:
                problems.append("missing config")
        elif draft.creatives or draft.config:
            problems.append("refusal still produced a campaign")

        mark = "FAIL" if problems else "ok  "
        print(f"{mark}  {expectation['match']}")
        print(f"      {draft.status}: {', '.join(recommended) or '—'}")
        if personas:
            print(f"      personas: {', '.join(personas)}")
        if problems:
            failed += 1
            print(f"      {'; '.join(problems)}")
            for row in draft.publishers[:8]:
                print(f"      {row.score:2d} {row.decision:<9} {row.publisher.name}")

    if len(examples) != len(EXPECTATIONS):
        print(f"Example count {len(examples)} != {len(EXPECTATIONS)}")
        failed += 1
    if failed:
        print(f"\n{failed} check(s) failed")
        return 1
    print("\nAll sample briefs passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
