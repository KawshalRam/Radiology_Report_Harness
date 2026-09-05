"""
Approximate local implementation of the RES (Radiology Edit Score) described in the
challenge overview, for calibrating our generation approach against train.csv before
generating test predictions.

IMPORTANT: this is our best-effort reconstruction of the published rules (text
normalization, weighted word-edit distance, field-aware FINDINGS score, IMPRESSION
score). The exact word-weight lists and unexpected-field penalty used by the real
Kaggle scorer are not published, so this is a calibration aid to catch regressions
and compare candidate reports directionally -- not a guaranteed match to the
leaderboard number.

Usage as a library:

    from res_scorer import score_report
    result = score_report(template_content, reference_report, candidate_report)
    print(result["RES"], result["F"], result["I"])

CLI:

    python3 res_scorer.py --data data/train.csv --predictions preds.csv
        preds.csv must have columns: case_id,report
        data.csv must have columns: case_id,template_content,report (reference)
"""
import argparse
import csv
import re
import sys
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Word classification / weights
# ---------------------------------------------------------------------------

NEGATION = {
    "no", "not", "none", "without", "free", "absent", "negative", "unremarkable",
    "normal", "nor",
}
LATERALITY = {
    "left", "right", "bilateral", "bilaterally", "unilateral", "midline",
}
SEVERITY_ACUITY = {
    "mild", "moderate", "severe", "marked", "minimal", "trace", "small", "large",
    "tiny", "massive", "acute", "chronic", "subacute", "stable", "new", "interval",
    "increased", "decreased", "worsening", "improving", "extensive", "focal",
    "diffuse", "high", "low", "grade", "advanced", "early",
}
UNITS = {"mm", "cm", "ml", "cc", "kg", "mg"}

FUNCTION_WORDS = {
    "the", "and", "of", "with", "a", "an", "is", "are", "was", "were", "in", "on",
    "at", "to", "for", "by", "or", "this", "these", "that", "there", "been", "be",
    "as", "which", "from", "it", "its", "than", "into", "over", "under", "within",
    "including", "than", "such",
}

W_CRITICAL = 4.00
W_CONTENT = 2.00
W_FUNCTION = 0.25

UNIT_SPELLINGS = {
    "millimeters": "mm", "millimeter": "mm", "millimetre": "mm", "millimetres": "mm",
    "centimeters": "cm", "centimeter": "cm", "centimetre": "cm", "centimetres": "cm",
    "milliliters": "ml", "milliliter": "ml",
}

NUM_RE = re.compile(r"^[+-]?\d+\.?\d*$")
TOKEN_RE = re.compile(r"[+-]?\d+\.?\d*|[a-z]+")
LEADING_MARKER_RE = re.compile(r"^\s*(?:\d+[\.\)]|[-*•])\s+")
LETTER_HYPHEN_LETTER_RE = re.compile(r"(?<=[a-zA-Z])-(?=[a-zA-Z])")


def token_weight(tok: str) -> float:
    if NUM_RE.match(tok):
        return W_CRITICAL
    if tok in UNITS:
        return W_CRITICAL
    if tok in NEGATION or tok in LATERALITY or tok in SEVERITY_ACUITY:
        return W_CRITICAL
    if tok in FUNCTION_WORDS:
        return W_FUNCTION
    return W_CONTENT


def normalize_and_tokenize(text: str):
    if text is None:
        return []
    text = unicodedata.normalize("NFKC", text).lower()
    lines = text.splitlines()
    cleaned_lines = [LEADING_MARKER_RE.sub("", ln) for ln in lines]
    text = " ".join(cleaned_lines)
    text = LETTER_HYPHEN_LETTER_RE.sub("", text)
    raw_tokens = TOKEN_RE.findall(text)
    tokens = [UNIT_SPELLINGS.get(t, t) for t in raw_tokens]
    return tokens


# ---------------------------------------------------------------------------
# Weighted word-level Levenshtein
# ---------------------------------------------------------------------------

def weighted_edit_cost(ref_tokens, sub_tokens):
    n, m = len(ref_tokens), len(sub_tokens)
    ref_w = [token_weight(t) for t in ref_tokens]
    sub_w = [token_weight(t) for t in sub_tokens]

    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + ref_w[i - 1]
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + sub_w[j - 1]

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_tokens[i - 1] == sub_tokens[j - 1]:
                sub_cost = dp[i - 1][j - 1]
            else:
                sub_cost = dp[i - 1][j - 1] + max(ref_w[i - 1], sub_w[j - 1])
            del_cost = dp[i - 1][j] + ref_w[i - 1]
            ins_cost = dp[i][j - 1] + sub_w[j - 1]
            dp[i][j] = min(sub_cost, del_cost, ins_cost)
    return dp[n][m]


def word_edit_score(reference_text: str, submitted_text: str) -> float:
    ref_tokens = normalize_and_tokenize(reference_text)
    sub_tokens = normalize_and_tokenize(submitted_text)
    if not ref_tokens and not sub_tokens:
        return 0.0
    cost = weighted_edit_cost(ref_tokens, sub_tokens)
    ref_w = sum(token_weight(t) for t in ref_tokens)
    sub_w = sum(token_weight(t) for t in sub_tokens)
    denom = max(ref_w, sub_w)
    if denom == 0:
        return 0.0
    return min(1.0, cost / denom)


# ---------------------------------------------------------------------------
# Report parsing: FINDINGS / IMPRESSION split, field-label extraction
# ---------------------------------------------------------------------------

LABEL_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /,.'\-]{1,60}):\s*(.*)$")


def split_sections(report: str):
    """Return (findings_text, impression_text)."""
    if not report:
        return "", ""
    m_f = re.search(r"FINDINGS\s*:", report, re.IGNORECASE)
    m_i = re.search(r"IMPRESSION\s*:", report, re.IGNORECASE)
    if not m_f or not m_i:
        return report, ""
    findings = report[m_f.end():m_i.start()]
    impression = report[m_i.end():]
    return findings.strip(), impression.strip()


def parse_fields(findings_text: str):
    """Return ordered list of (label, content) plus a special '' label for any
    unlabelled preamble content before the first field header."""
    fields = []
    preamble_lines = []
    current_label = None
    current_lines = []

    def flush():
        if current_label is not None:
            fields.append((current_label, "\n".join(current_lines).strip()))

    for line in findings_text.splitlines():
        m = LABEL_LINE_RE.match(line)
        if m:
            flush()
            current_label = m.group(1).strip().upper()
            current_lines = [m.group(2)] if m.group(2) else []
        else:
            if current_label is None:
                preamble_lines.append(line)
            else:
                current_lines.append(line)
    flush()

    preamble = "\n".join(preamble_lines).strip()
    return preamble, fields


# ---------------------------------------------------------------------------
# Field-aware FINDINGS score + IMPRESSION score + RES_case
# ---------------------------------------------------------------------------

CHANGED_EPS = 0.02


def score_report(template_content: str, reference_report: str, candidate_report: str):
    _, template_fields = parse_fields(split_sections(template_content)[0]
                                       if "IMPRESSION" in (template_content or "").upper()
                                       else (template_content or ""))
    # template_content may itself be FINDINGS-only (no IMPRESSION); handle both.
    if not template_fields:
        _, template_fields = parse_fields(template_content or "")
    template_map = {label: content for label, content in template_fields}

    ref_findings, ref_impression = split_sections(reference_report)
    cand_findings, cand_impression = split_sections(candidate_report)

    ref_preamble, ref_fields = parse_fields(ref_findings)
    cand_preamble, cand_fields = parse_fields(cand_findings)
    cand_map = {label: content for label, content in cand_fields}
    cand_labels_seen = set()

    weighted_sum = 0.0
    weight_total = 0.0
    field_details = []

    for label, ref_content in ref_fields:
        template_content_for_label = template_map.get(label, "")
        changed_score = word_edit_score(template_content_for_label, ref_content)
        f_weight = 3.0 if changed_score > CHANGED_EPS else 1.0

        cand_content = cand_map.get(label)
        if cand_content is None:
            edit = word_edit_score(ref_content, "")
            present = False
        else:
            edit = word_edit_score(ref_content, cand_content)
            present = True
            cand_labels_seen.add(label)

        weighted_sum += f_weight * edit
        weight_total += f_weight
        field_details.append({
            "label": label, "field_weight": f_weight, "edit_score": edit,
            "present_in_candidate": present,
        })

    # Unexpected fields present in candidate but not in reference: penalize.
    extra_details = []
    for label, cand_content in cand_fields:
        if label in {l for l, _ in ref_fields}:
            continue
        edit = word_edit_score("", cand_content)
        weighted_sum += 1.0 * edit
        weight_total += 1.0
        extra_details.append({"label": label, "edit_score": edit})

    # Unsupported unlabelled preamble content in candidate not present in reference.
    if cand_preamble and word_edit_score(ref_preamble, cand_preamble) > CHANGED_EPS:
        edit = word_edit_score(ref_preamble, cand_preamble)
        weighted_sum += 1.0 * edit
        weight_total += 1.0

    F = weighted_sum / weight_total if weight_total else 0.0
    I = word_edit_score(ref_impression, cand_impression)
    RES = 0.65 * F + 0.35 * I

    return {
        "RES": RES, "F": F, "I": I,
        "field_details": field_details,
        "extra_fields": extra_details,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="CSV with case_id,template_content,report (reference)")
    ap.add_argument("--predictions", required=True, help="CSV with case_id,report (candidate)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    ref_rows = {}
    with open(args.data, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ref_rows[row["case_id"]] = row

    pred_rows = {}
    with open(args.predictions, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pred_rows[row["case_id"]] = row["report"]

    scores = []
    for case_id, cand in pred_rows.items():
        if case_id not in ref_rows:
            print(f"WARNING: {case_id} not found in reference data", file=sys.stderr)
            continue
        ref = ref_rows[case_id]
        result = score_report(ref["template_content"], ref["report"], cand)
        scores.append(result["RES"])
        if args.verbose:
            print(f"{case_id}  RES={result['RES']:.4f}  F={result['F']:.4f}  I={result['I']:.4f}")
            for fd in result["field_details"]:
                flag = "" if fd["present_in_candidate"] else "  <-- MISSING"
                print(f"    [{fd['label']}] w={fd['field_weight']} edit={fd['edit_score']:.3f}{flag}")
            for ex in result["extra_fields"]:
                print(f"    UNEXPECTED FIELD [{ex['label']}] edit={ex['edit_score']:.3f}")

    if scores:
        print(f"\nMean RES over {len(scores)} cases: {sum(scores)/len(scores):.4f}")


if __name__ == "__main__":
    main()
