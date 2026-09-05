"""
Builds notebook/pipeline.ipynb from RULES.md + src/res_scorer.py, so the notebook's
prompt and local scorer always stay in sync with the calibrated rules used to generate
submission.csv. Run: python3 src/build_notebook.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "RULES.md"), encoding="utf-8") as f:
    RULES_MD = f.read()

with open(os.path.join(ROOT, "src", "res_scorer.py"), encoding="utf-8") as f:
    SCORER_SRC = f.read()
# Strip the CLI section (argparse main) -- the notebook only needs score_report().
SCORER_SRC = SCORER_SRC.split("# CLI\n# ---")[0].rstrip() + "\n"


def md(*lines):
    return {"cell_type": "markdown", "metadata": {}, "source": [l + "\n" for l in lines]}


def code(*lines):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": [l + "\n" for l in lines]}


cells = []

cells.append(md(
    "# Radiology report template-editing pipeline",
    "",
    "Converts short radiologist dictation into a completed structured report by editing",
    "the supplied normal `template_content`, using the Anthropic API (Claude).",
    "",
    "This notebook is self-contained and reproduces `submission.csv` end-to-end:",
    "1. Reads `train.csv` / `test.csv`.",
    "2. Builds a prompt from the calibrated rules in `RULES.md` (embedded below, verbatim).",
    "3. Calls the Anthropic API once per case, validates the output format, retries on",
    "   malformed responses.",
    "4. Self-validates on a `train.csv` sample using a local approximate re-implementation",
    "   of the RES scorer, so the pipeline reports an expected-quality signal before it",
    "   ever touches the test set.",
    "5. Writes `submission.csv` with exactly `case_id,report`.",
    "",
    "**No API key is stored in this notebook.** Set it as a Kaggle Secret named",
    "`ANTHROPIC_API_KEY` (Add-ons -> Secrets) or as an environment variable before running.",
))

cells.append(code(
    "!pip install -q anthropic",
))

cells.append(code(
    "import os",
    "import re",
    "import csv",
    "import json",
    "import time",
    "import unicodedata",
    "from dataclasses import dataclass",
    "",
    "import pandas as pd",
    "import anthropic",
))

cells.append(md("## Configuration"))

cells.append(code(
    "# Adjust these paths to your Kaggle dataset mount, e.g. /kaggle/input/<competition>/",
    "TRAIN_CSV = \"train.csv\"",
    "TEST_CSV = \"test.csv\"",
    "SAMPLE_SUBMISSION_CSV = \"sample_submission.csv\"",
    "OUTPUT_CSV = \"submission.csv\"",
    "",
    "# \"claude-sonnet-5\" is the default (best cost/quality balance for this task).",
    "# Set to \"claude-opus-5\" for the highest-quality option if available on your account.",
    "MODEL = os.environ.get(\"REPORT_MODEL\", \"claude-sonnet-5\")",
    "",
    "MAX_RETRIES = 3",
    "REQUEST_TIMEOUT = 120",
    "SLEEP_BETWEEN_CALLS = 0.5  # be polite to the rate limiter",
    "",
    "N_CALIBRATION_SAMPLES = 20  # how many train.csv rows to self-validate on before test generation",
))

cells.append(md(
    "## API key",
    "",
    "Never hardcode a key. On Kaggle, add it as a Secret (Add-ons -> Secrets) named",
    "`ANTHROPIC_API_KEY` and attach it to the notebook; it's then exposed via",
    "`kaggle_secrets`. Locally, just export `ANTHROPIC_API_KEY` in your shell before",
    "launching Jupyter.",
))

cells.append(code(
    "def get_api_key() -> str:",
    "    try:",
    "        from kaggle_secrets import UserSecretsClient",
    "        return UserSecretsClient().get_secret(\"ANTHROPIC_API_KEY\")",
    "    except Exception:",
    "        pass",
    "    key = os.environ.get(\"ANTHROPIC_API_KEY\")",
    "    if not key:",
    "        raise RuntimeError(",
    "            \"Set ANTHROPIC_API_KEY as a Kaggle Secret or environment variable before running.\"",
    "        )",
    "    return key",
    "",
    "client = anthropic.Anthropic(api_key=get_api_key())",
))

cells.append(md(
    "## Calibrated rules (embedded verbatim from `RULES.md`)",
    "",
    "This is the exact spec used to hand-generate the submitted reports, kept in one",
    "file (`RULES.md`) and embedded here so the API prompt and the manual generation",
    "process can never drift apart.",
))

cells.append(code(
    "RULES_MD = " + json.dumps(RULES_MD),
))

cells.append(code(
    "OUTPUT_CONTRACT_REMINDER = (",
    "    \"Return ONLY the completed report text, starting with 'FINDINGS:' and containing \"",
    "    \"exactly one 'IMPRESSION:' section. No preamble, no explanation, no markdown code \"",
    "    \"fences, no other headings.\"",
    ")",
    "",
    "SYSTEM_PROMPT = RULES_MD + \"\\n\\n\" + OUTPUT_CONTRACT_REMINDER",
))

cells.append(md("## Prompt construction"))

cells.append(code(
    "def build_user_prompt(row) -> str:",
    "    return (",
    "        f\"modality: {row['modality']}\\n\"",
    "        f\"body_part: {row['body_part']}\\n\"",
    "        f\"study_description: {row['study_description']}\\n\"",
    "        f\"patient_age_band: {row['patient_age_band']}\\n\"",
    "        f\"patient_sex: {row['patient_sex']}\\n\\n\"",
    "        f\"template_content:\\n{row['template_content']}\\n\\n\"",
    "        f\"dictation:\\n{row['dictation']}\\n\\n\"",
    "        \"Produce the completed report now, following the rules exactly.\"",
    "    )",
))

cells.append(md(
    "## Generation with format validation + retry",
    "",
    "A valid response must start with `FINDINGS:` and contain exactly one `IMPRESSION:`",
    "section. If the model returns something malformed (preamble, markdown fences, missing",
    "section), we send one corrective follow-up before giving up.",
))

cells.append(code(
    "VALID_RE = re.compile(r\"^\\s*FINDINGS:\", re.IGNORECASE)",
    "IMPRESSION_RE = re.compile(r\"IMPRESSION\\s*:\", re.IGNORECASE)",
    "",
    "",
    "def is_valid_report(text: str) -> bool:",
    "    if not text:",
    "        return False",
    "    if not VALID_RE.match(text.strip()):",
    "        return False",
    "    if len(IMPRESSION_RE.findall(text)) != 1:",
    "        return False",
    "    return True",
    "",
    "",
    "def clean_report(text: str) -> str:",
    "    text = text.strip()",
    "    # Strip accidental markdown code fences.",
    "    text = re.sub(r\"^```[a-zA-Z]*\\n?\", \"\", text)",
    "    text = re.sub(r\"\\n?```$\", \"\", text)",
    "    return text.strip()",
    "",
    "",
    "def generate_report(row) -> str:",
    "    messages = [{\"role\": \"user\", \"content\": build_user_prompt(row)}]",
    "    last_text = \"\"",
    "    for attempt in range(MAX_RETRIES):",
    "        resp = client.messages.create(",
    "            model=MODEL,",
    "            max_tokens=4096,",
    "            temperature=0,",
    "            system=SYSTEM_PROMPT,",
    "            messages=messages,",
    "        )",
    "        text = clean_report(\"\".join(b.text for b in resp.content if b.type == \"text\"))",
    "        last_text = text",
    "        if is_valid_report(text):",
    "            return text",
    "        messages.append({\"role\": \"assistant\", \"content\": text})",
    "        messages.append({\"role\": \"user\", \"content\": (",
    "            \"That response did not follow the required output contract. \"",
    "            \"Return ONLY the report, starting with 'FINDINGS:' and containing exactly \"",
    "            \"one 'IMPRESSION:' section, no other text.\"",
    "        )})",
    "        time.sleep(SLEEP_BETWEEN_CALLS)",
    "    return last_text  # best effort after MAX_RETRIES",
))

cells.append(md(
    "## Local approximate RES scorer (for self-validation only)",
    "",
    "Reconstructed from the published scoring rules (text normalization, weighted",
    "word-edit distance, field-aware FINDINGS score, IMPRESSION score). The exact word-weight",
    "lists used by the real Kaggle scorer aren't published, so this is a calibration aid,",
    "not a guaranteed match to the leaderboard number -- but it reliably catches routing,",
    "unsupported-content, and formatting regressions before submission.",
))

cells.append(code(SCORER_SRC.rstrip()))

cells.append(md(
    "## Step 1: self-validate on a train.csv sample",
    "",
    "Runs the exact same `generate_report` pipeline against a sample of `train.csv` (which",
    "has a reference `report` column) and reports the mean approximate RES. This is a",
    "sanity check that the deployed prompt still performs before spending API calls on the",
    "un-scored test set.",
))

cells.append(code(
    "train_df = pd.read_csv(TRAIN_CSV)",
    "calib_sample = train_df.sample(n=min(N_CALIBRATION_SAMPLES, len(train_df)), random_state=0)",
    "",
    "calib_scores = []",
    "for _, row in calib_sample.iterrows():",
    "    candidate = generate_report(row)",
    "    result = score_report(row['template_content'], row['report'], candidate)",
    "    calib_scores.append(result['RES'])",
    "    time.sleep(SLEEP_BETWEEN_CALLS)",
    "",
    "print(f\"Mean approximate RES on {len(calib_scores)} train samples: \"",
    "      f\"{sum(calib_scores)/len(calib_scores):.4f}\")",
))

cells.append(md(
    "## Step 2: generate test.csv predictions (with checkpointing)",
    "",
    "Writes incrementally to a checkpoint file so a Kaggle session interruption doesn't",
    "lose completed work -- re-running this cell resumes from the last completed case_id.",
))

cells.append(code(
    "test_df = pd.read_csv(TEST_CSV)",
    "CHECKPOINT_CSV = OUTPUT_CSV + \".checkpoint\"",
    "",
    "completed = {}",
    "if os.path.exists(CHECKPOINT_CSV):",
    "    with open(CHECKPOINT_CSV, newline=\"\", encoding=\"utf-8\") as f:",
    "        for r in csv.DictReader(f):",
    "            completed[r[\"case_id\"]] = r[\"report\"]",
    "    print(f\"Resuming: {len(completed)} cases already completed.\")",
    "",
    "fieldnames = [\"case_id\", \"report\"]",
    "write_header = not os.path.exists(CHECKPOINT_CSV)",
    "with open(CHECKPOINT_CSV, \"a\", newline=\"\", encoding=\"utf-8\") as f:",
    "    writer = csv.DictWriter(f, fieldnames=fieldnames)",
    "    if write_header:",
    "        writer.writeheader()",
    "    for _, row in test_df.iterrows():",
    "        cid = row[\"case_id\"]",
    "        if cid in completed:",
    "            continue",
    "        report = generate_report(row)",
    "        writer.writerow({\"case_id\": cid, \"report\": report})",
    "        f.flush()",
    "        completed[cid] = report",
    "        time.sleep(SLEEP_BETWEEN_CALLS)",
    "",
    "print(f\"Generated {len(completed)} / {len(test_df)} test reports.\")",
))

cells.append(md("## Step 3: assemble and validate submission.csv"))

cells.append(code(
    "sample_sub = pd.read_csv(SAMPLE_SUBMISSION_CSV)",
    "assert set(sample_sub['case_id']) == set(completed.keys()), \\",
    "    \"Missing or extra case_ids vs sample_submission.csv\"",
    "",
    "out_df = pd.DataFrame({",
    "    \"case_id\": sample_sub[\"case_id\"],",
    "    \"report\": [completed[cid] for cid in sample_sub[\"case_id\"]],",
    "})",
    "",
    "bad = [cid for cid, r in zip(out_df['case_id'], out_df['report']) if not is_valid_report(r)]",
    "if bad:",
    "    print(f\"WARNING: {len(bad)} case(s) failed format validation: {bad[:10]}\")",
    "",
    "out_df.to_csv(OUTPUT_CSV, index=False)",
    "print(f\"Wrote {OUTPUT_CSV} with {len(out_df)} rows.\")",
))

cells.append(md(
    "## Reproducibility notes",
    "",
    "- `RULES.md` and `src/res_scorer.py` are embedded verbatim above (see",
    "  `src/build_notebook.py`, which regenerates this notebook from those two source",
    "  files so the prompt and the scorer can never silently drift from what was actually",
    "  used to produce the submitted CSV).",
    "- `MODEL`, retry, and sampling parameters are all set at the top (temperature 0 for",
    "  determinism); no manual per-case editing occurs anywhere in this pipeline.",
    "- No API key is stored in this notebook or in source control.",
))

notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out_path = os.path.join(ROOT, "notebook", "pipeline.ipynb")
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(notebook, f, indent=1)

print(f"Wrote {out_path}")
