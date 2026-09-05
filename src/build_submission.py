"""
Assembles the 8 batch_*_out.json files (produced by parallel generation agents) into
submission.csv, validating structure/format along the way.

Usage: python3 src/build_submission.py
"""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = "/tmp/claude-0/-home-user-Radiology-Report-Harness/2e5876c2-dc56-5b31-83c9-9fa34904bd6a/scratchpad"

sys.path.insert(0, os.path.join(ROOT, "src"))
from res_scorer import split_sections, parse_fields, LABEL_LINE_RE  # noqa: E402

N_BATCHES = 8
PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{0,40}\]")


def load_test_rows():
    import csv as csvmod
    rows = {}
    with open(os.path.join(ROOT, "data", "test.csv"), newline="", encoding="utf-8-sig") as f:
        for row in csvmod.DictReader(f):
            rows[row["case_id"]] = row
    return rows


def main():
    test_rows = load_test_rows()
    reports = {}
    for i in range(N_BATCHES):
        path = os.path.join(SCRATCH, f"batch_{i}_out.json")
        if not os.path.exists(path):
            print(f"MISSING: {path}", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            batch = json.load(f)
        overlap = set(batch) & set(reports)
        if overlap:
            print(f"WARNING: duplicate case_ids across batches: {overlap}", file=sys.stderr)
        reports.update(batch)

    missing = set(test_rows) - set(reports)
    extra = set(reports) - set(test_rows)
    if missing:
        print(f"ERROR: {len(missing)} test case_ids have no generated report: {sorted(missing)[:10]}",
              file=sys.stderr)
    if extra:
        print(f"WARNING: {len(extra)} generated case_ids not in test.csv: {sorted(extra)[:10]}",
              file=sys.stderr)

    problems = []
    for cid, report in reports.items():
        if cid not in test_rows:
            continue
        row = test_rows[cid]
        issues = []

        if not re.match(r"^\s*FINDINGS:", report, re.IGNORECASE):
            issues.append("does not start with FINDINGS:")
        impression_count = len(re.findall(r"IMPRESSION\s*:", report, re.IGNORECASE))
        if impression_count != 1:
            issues.append(f"has {impression_count} IMPRESSION: sections (expected 1)")

        if PLACEHOLDER_RE.search(report):
            issues.append(f"leftover bracket placeholder: {PLACEHOLDER_RE.findall(report)}")

        findings_text, impression_text = split_sections(report)
        _, cand_fields = parse_fields(findings_text)
        cand_labels = {label for label, _ in cand_fields}

        template_text = row["template_content"]
        template_findings_text, _ = split_sections(template_text)
        _, template_fields = parse_fields(template_findings_text)
        template_labels = {label for label, _ in template_fields}

        missing_labels = template_labels - cand_labels
        if missing_labels:
            issues.append(f"missing template field label(s): {sorted(missing_labels)}")

        if not impression_text.strip():
            issues.append("empty IMPRESSION section")
        if not findings_text.strip():
            issues.append("empty FINDINGS section")

        if issues:
            problems.append((cid, issues))

    print(f"Loaded {len(reports)} generated reports for {len(test_rows)} test cases.")
    if problems:
        print(f"\n{len(problems)} case(s) with potential issues:")
        for cid, issues in problems[:40]:
            print(f"  {cid}: {'; '.join(issues)}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
    else:
        print("No structural issues detected.")

    if missing:
        print("\nAborting submission.csv write: missing case_ids. Fix batches and re-run.",
              file=sys.stderr)
        sys.exit(1)

    out_path = os.path.join(ROOT, "submission.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "report"])
        for cid in test_rows:  # preserve test.csv / sample_submission.csv order
            writer.writerow([cid, reports[cid]])

    print(f"\nWrote {out_path} with {len(test_rows)} rows.")


if __name__ == "__main__":
    main()
