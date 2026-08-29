"""
Moderator-facing performance/accuracy report (docs/decisions/0027) -- prints
`cli.tools.get_stats()` as markdown: decision distribution, moderator override rate,
agent confidence, pipeline latency, failure rate, policy rule hit counts.

Usage:
    python3 scripts/pipeline_stats.py               # all-time
    python3 scripts/pipeline_stats.py --since 2026-08-25T00:00:00Z
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.tools import get_stats  # noqa: E402


def _pct(n: float | None) -> str:
    return "n/a" if n is None else f"{n:.1%}"


def _num(n: float | None, fmt: str = "{:.2f}") -> str:
    return "n/a" if n is None else fmt.format(n)


def render(stats: dict) -> str:
    lines = []
    scope = f"since {stats['since']}" if stats["since"] else "all-time"
    lines.append(f"# Pipeline stats ({scope})\n")

    lines.append("## Listings by status")
    if stats["listingsByStatus"]:
        for status, n in sorted(stats["listingsByStatus"].items()):
            lines.append(f"- {status}: {n}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Automated decisions (first fusion-v1 DecisionAgent artifact per listing)")
    if stats["automatedDecisionCounts"]:
        for decision, n in sorted(stats["automatedDecisionCounts"].items()):
            lines.append(f"- {decision}: {n}")
    else:
        lines.append("- (none)")
    lines.append(f"- avg confidence: {_num(stats['automatedAvgConfidence'])}")
    lines.append("")

    lines.append("## Human review")
    lines.append(f"- listings with a recorded moderator APPROVE/REJECT verdict: {stats['humanReviewedCount']}")
    if stats["humanReviewOutcomes"]:
        lines.append("- outcomes for automated-REVIEW listings:")
        for decision, n in sorted(stats["humanReviewOutcomes"].items()):
            lines.append(f"  - {decision}: {n}")
    lines.append(
        f"- overridden (moderator decision differs from an automated APPROVE/REJECT): "
        f"{stats['overriddenCount']} ({_pct(stats['overrideRate'])} of reviewed)"
    )
    lines.append("")

    lines.append("## Signal quality")
    lines.append(f"- avg Safety Agent confidence: {_num(stats['avgSafetyConfidence'])}")
    lines.append(f"- avg Consistency Agent inconsistency score: {_num(stats['avgInconsistencyScore'])}")
    lines.append(f"- avg automated pipeline latency: {_num(stats['avgPipelineLatencySeconds'], '{:.1f}s')}")
    lines.append("")

    lines.append("## Policy rule hits")
    if stats["policyRuleHits"]:
        for rule, n in sorted(stats["policyRuleHits"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {rule}: {n}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Pipeline failures")
    if stats["failuresByError"]:
        for row in stats["failuresByError"]:
            lines.append(f"- {row['n']}x: {row['error']}")
    else:
        lines.append("- (none)")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO timestamp; only listings created at/after this are counted")
    args = parser.parse_args()

    stats = get_stats(since=args.since)
    print(render(stats))


if __name__ == "__main__":
    main()
