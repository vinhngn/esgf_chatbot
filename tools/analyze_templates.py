"""
Analyze hardcoded Cypher templates and surface:
1. shared prompt scaffolding,
2. recurring rule families,
3. domain-specific rule fragments,
4. a compact universal-core proposal.

This script is meant for research/paper work: it produces a repeatable report
instead of relying on ad-hoc manual inspection.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_FILE = ROOT / "templates" / "cypher_templates.py"


TEMPLATE_PATTERN = re.compile(
    r"(?P<name>CYPHER_GENERATION_[A-Z_]+_TEMPLATE)\s*=\s*\"\"\"\n(?P<body>.*?)\n\"\"\"",
    re.DOTALL,
)
RULE_PATTERN = re.compile(
    r"^RULE\s+\d+\s*-\s*(?P<title>.+)$",
    re.MULTILINE,
)
EXAMPLE_HEADER_PATTERN = re.compile(
    r"^###\s+(?P<title>.+?)\s+###$",
    re.MULTILINE,
)


def normalize_template_name(name: str) -> str:
    prefix = "CYPHER_GENERATION_"
    suffix = "_TEMPLATE"
    return name[len(prefix) : -len(suffix)].lower()


def extract_templates(text: str) -> dict[str, str]:
    return {
        normalize_template_name(match.group("name")): match.group("body")
        for match in TEMPLATE_PATTERN.finditer(text)
    }


def split_rules(body: str) -> list[dict[str, object]]:
    matches = list(RULE_PATTERN.finditer(body))
    rules: list[dict[str, object]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else body.find("=== FEW-SHOT EXAMPLES ===")
        if end == -1:
            end = len(body)
        block = body[start:end].strip()
        bullets = [
            line.strip()[2:].strip()
            for line in block.splitlines()
            if line.strip().startswith("- ")
        ]
        rules.append(
            {
                "title": match.group("title").strip(),
                "bullets": bullets,
                "raw": block,
            }
        )
    return rules


def extract_example_sections(body: str) -> list[str]:
    return [match.group("title").strip() for match in EXAMPLE_HEADER_PATTERN.finditer(body)]


def classify_rule(title: str, bullets: list[str]) -> str:
    text = " ".join([title, *bullets]).lower()
    families = [
        ("output_contract", ("output", "markdown", "explanation", "empty response")),
        ("return_shape", ("return format", "return full node", "return column", "alias")),
        ("aggregation", ("count", "avg", "sum", "aggregation", "most", "least")),
        ("ordering_limit", ("order by", "limit", "top n", "first n")),
        ("schema_grounding", ("node label", "relationship direction", "property location", ":me vs :user")),
        ("filtering", ("date", "year", "string matching", "null handling", "type conversion")),
        ("pattern_library", ("optional match", "exists", "not exists", "shared components", "retweets")),
    ]
    for family, keywords in families:
        if any(keyword in text for keyword in keywords):
            return family
    return "other"


def build_report(templates: dict[str, str]) -> dict[str, object]:
    shared_rule_titles = Counter()
    family_counts = Counter()
    example_sections = Counter()
    per_template: dict[str, object] = {}
    bullet_occurrence = Counter()

    for name, body in templates.items():
        rules = split_rules(body)
        sections = extract_example_sections(body)
        families = Counter()

        for rule in rules:
            title = str(rule["title"])
            shared_rule_titles[title] += 1
            family = classify_rule(title, list(rule["bullets"]))
            families[family] += 1
            family_counts[family] += 1
            for bullet in rule["bullets"]:
                bullet_occurrence[bullet] += 1

        for section in sections:
            example_sections[section] += 1

        per_template[name] = {
            "rule_count": len(rules),
            "example_section_count": len(sections),
            "example_sections": sections,
            "rule_families": dict(families),
            "domain_specific_signals": detect_domain_specific_signals(name, body),
        }

    common_titles = [title for title, count in shared_rule_titles.items() if count >= 2]
    repeated_bullets = [bullet for bullet, count in bullet_occurrence.items() if count >= 2]

    universal_core = [
        "Output only raw Cypher with no markdown or explanation.",
        "Ground labels, relationships, and property location from schema before generating queries.",
        "Infer return shape from question intent: full node, named properties, metric, or mixed result.",
        "Handle ranking and retrieval separately: `top/most` implies ordering; `first` often implies plain limit.",
        "Use aggregation patterns explicitly for count/avg/sum/min/max questions.",
        "Prefer DISTINCT only when joins can duplicate requested entities.",
        "Add schema-aware filters for time, nulls, and numeric casts when property types demand them.",
        "Preserve alias names and return-column contracts when the question implies a report shape.",
        "Keep a small pattern library for EXISTS, OPTIONAL MATCH, and direction-sensitive relationship traversals.",
    ]
    candidate_generalized_template = [
        "You are an expert Neo4j Cypher generator.",
        "CRITICAL: Output ONLY the raw Cypher query. NO markdown. NO explanation.",
        "",
        "=== SCHEMA ===",
        "{schema}",
        "",
        "=== UNIVERSAL CORE ===",
        "- Ground labels, relationships, and properties from schema.",
        "- Infer return shape from question intent.",
        "- Separate ranking from simple retrieval.",
        "- Use aggregation explicitly for count/avg/sum/min/max requests.",
        "- Use DISTINCT only when joins duplicate requested entities.",
        "- Apply NULL checks, numeric casts, and temporal filters when required.",
        "- Preserve aliases when the question implies a report format.",
        "- Prefer simple schema-faithful traversals.",
        "",
        "=== DOMAIN SUPPLEMENT ===",
        "{domain_supplement}",
        "",
        "=== STYLE HINTS ===",
        "{few_shot_hints}",
        "",
        "{question}",
    ]

    return {
        "template_file": str(TEMPLATE_FILE),
        "template_count": len(templates),
        "shared_rule_titles": sorted(common_titles),
        "repeated_rule_bullets": sorted(repeated_bullets)[:40],
        "family_counts": dict(family_counts),
        "example_sections": dict(example_sections),
        "per_template": per_template,
        "universal_core_proposal": universal_core,
        "candidate_generalized_template": "\n".join(candidate_generalized_template),
    }


def detect_domain_specific_signals(name: str, body: str) -> list[str]:
    signals_by_template = {
        "climate": ["pr", "tas", "rcm", "aogcm", "realm", "experiment", "florida"],
        "movies": ["acted_in", "reviewed", "roles", "nancy meyers", "matrix"],
        "recommendations": ["imdbrating", "revenue", "budget", "userid", "tmdb"],
        "northwind": ["supplier", "product", "order", "freight", "discount"],
        "twitter": ["screen_name", "amplifies", "retweets", "mentions", "betweenness"],
    }
    lowered = body.lower()
    return [
        token
        for token in signals_by_template.get(name, [])
        if token in lowered
    ]


def render_markdown(report: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("# Cypher Template Analysis")
    lines.append("")
    lines.append(f"- Template file: `{report['template_file']}`")
    lines.append(f"- Template count: `{report['template_count']}`")
    lines.append("")
    lines.append("## Shared Rule Titles")
    for title in report["shared_rule_titles"]:
        lines.append(f"- {title}")
    lines.append("")
    lines.append("## Universal Core Proposal")
    for item in report["universal_core_proposal"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Candidate Generalized Template")
    lines.append("```text")
    lines.append(report["candidate_generalized_template"])
    lines.append("```")
    lines.append("")
    lines.append("## Rule Family Counts")
    for family, count in sorted(report["family_counts"].items()):
        lines.append(f"- {family}: {count}")
    lines.append("")
    lines.append("## Per Template")
    for template_name, details in report["per_template"].items():
        lines.append(f"### {template_name}")
        lines.append(f"- rule_count: {details['rule_count']}")
        lines.append(f"- example_section_count: {details['example_section_count']}")
        lines.append(
            "- rule_families: "
            + ", ".join(
                f"{family}={count}"
                for family, count in sorted(details["rule_families"].items())
            )
        )
        signals = details["domain_specific_signals"]
        lines.append(f"- domain_specific_signals: {', '.join(signals) if signals else 'none'}")
        sections = details["example_sections"]
        if sections:
            lines.append(f"- example_sections: {', '.join(sections[:10])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=("json", "md"),
        default="md",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output file path.",
    )
    args = parser.parse_args()

    text = TEMPLATE_FILE.read_text(encoding="utf-8")
    templates = extract_templates(text)
    report = build_report(templates)

    if args.format == "json":
        output_text = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        output_text = render_markdown(report)

    if args.output:
        args.output.write_text(output_text, encoding="utf-8")
    else:
        print(output_text)


if __name__ == "__main__":
    main()
