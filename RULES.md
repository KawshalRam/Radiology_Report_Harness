# Template-editing rules (shared spec)

This is the single source of truth for turning `(modality, body_part, study_description,
patient_age_band, patient_sex, template_content, dictation)` into a completed report. It is
used verbatim by:

1. Manual/agentic generation (Claude Code reasoning through cases directly), and
2. The Kaggle notebook's API prompt (`notebook/pipeline.ipynb`).

Keeping one wording for both means the notebook's output should closely match what was
generated manually, which matters for the "must reproduce the uploaded CSV without manual
case-level editing" requirement.

## Output contract

Return **only**:

```
FINDINGS:
<fields, in the same order and with the same field labels as template_content>

IMPRESSION:
<concise summary>
```

No preamble, no explanation, no markdown fences, no extra sections. Do not invent a field
label that isn't in `template_content`. Do not drop a field label that is in
`template_content` — if the dictation says nothing about it, keep the template's normal
sentence for that field unchanged.

## Formatting rules (mechanical, apply every time)

- **Field labels are always rendered in ALL CAPS** in the output, even if `template_content`
  wrote them in Title Case or mixed case (e.g. template `Hip Joints:` → output
  `HIP JOINTS:`; template `Medial meniscus:` → output `MEDIAL MENISCUS:`). This is a
  systematic transformation applied to every field label, independent of content changes.
- **Only emit `FINDINGS:` and `IMPRESSION:`.** Some templates carry extra boilerplate after
  the normal fields — a legend/menu of categories (e.g. a full Lung-RADS category
  reference table), a `RECOMMENDATION:` placeholder, blank `[]` placeholders, a scoring
  key, version/date footer text. None of that is a field to preserve — drop it entirely.
  Only real anatomic/system FINDINGS fields and the two required section headers survive
  into the output.
- If the dictation already supplies a clean, well-organized impression (a short list of
  numbered/plain conclusions), reuse it close to verbatim rather than rewriting it in your
  own words — don't paraphrase good dictated prose for its own sake.

## Core rules

1. **The template is the starting report, not an example.** Start from
   `template_content` and edit it — don't write a report from scratch.
2. **Route every dictated finding to the matching FINDINGS field.** Read the dictation
   fully first, then decide which template field each finding belongs to by anatomy/topic,
   not by the order it was dictated in. A finding about the pleura goes under the pleura
   field even if it was dictated before a lung finding.
3. **Replace or modify the normal statement when the dictation contradicts it.** If a field's
   template sentence is a normal/negative statement ("no acute fracture", "tendon is intact")
   and the dictation describes an abnormality in that anatomic area, edit that sentence to
   state the abnormality. Keep any part of the template sentence that is still true (e.g. if
   only one of two structures in a combined field is abnormal, keep the negative statement
   for the structure that's still normal).
4. **Preserve untouched fields verbatim — but "untouched" means the dictation said
   nothing about that anatomy, not "the finding is normal".** If the dictation says
   nothing that bears on a field, leave that field's template wording exactly as-is: don't
   paraphrase, reorder, or "polish" it.
   **However**, if the dictation *does* narrate that anatomy — even just to confirm it's
   normal, using its own specific wording (naming the exact vessels/structures examined,
   phrases like "where visualized", "grossly clear", extra qualifiers) — use the
   dictation's phrasing for that field instead of the template's generic sentence, even
   though the clinical conclusion (still normal) hasn't changed. Reference reports
   consistently rewrite normal fields to match a detailed dictation's own words rather than
   leaving the boilerplate in place; only fields the dictation is silent on keep the exact
   template sentence. A terse/blanket dictation ("normal", "normal chest") with no
   per-field detail gives nothing to substitute, so every field then stays as template text.
5. **Do not add unsupported content.** Every clinical statement in the output must trace to
   either the dictation or the template's own normal wording. Never introduce a finding,
   measurement, or diagnosis that isn't in one of those two sources. Vitals like "thank you
   for your referral", "correlate clinically" instructions, or dictation preamble about the
   technique/history are not findings — leave them out unless the template already has a
   place for exam-quality/technique remarks.
6. **Route every dictated finding somewhere, even ones with no matching field — normal
   findings included.** If the template has an `OTHER FINDINGS:` field, put any dictated
   finding (abnormal *or* normal/negative) that doesn't cleanly belong to another labelled
   field there. Leave it empty (no content after the label) only if truly nothing applies —
   never delete the label itself. If the template has **no** `OTHER FINDINGS:` field and a
   dictated finding still has no matching labelled field (e.g. the dictation describes an
   organ system the template doesn't break out, like incidental brain findings on a
   pituitary-focused MRI, or visualized nerves on a joint MRI), add it as an unlabelled
   paragraph inside the FINDINGS section — wherever it reads naturally, typically right
   after the last labelled field (or, for exam-quality/technique remarks, before the first
   field). Do not silently drop a dictated finding just because no field name fits it.
7. **Resolve placeholders.** Templates sometimes contain bracket placeholders such as
   `[left/right]`, `[_laterality_]`, `[normal/abnormal]`. Replace these with the actual value
   supported by the dictation (e.g. "either lower extremity" for bilateral, "the right
   shoulder" for unilateral-right). Never leave literal brackets in the output.
8. **Laterality and negation are high-value tokens — get them exactly right.** "No pleural
   effusion" vs "small right pleural effusion" is the entire point of the exercise; a wrong
   negation or wrong side is penalized heavily by the scorer's critical-word weighting.
9. **Preserve template wording and field order whenever possible.** When you do need to
   state an abnormality, prefer editing the existing template sentence (swap the normal
   claim for the abnormal one, keep the surrounding phrasing/structure) over rewriting the
   whole field in new prose. Keep the fields in the same top-to-bottom order as
   `template_content`.
10. **IMPRESSION is a concise summary, not a repeat of FINDINGS.** State only the
    clinically important abnormal findings (typically as a short numbered list when there
    are multiple), optionally with a supported differential/likely diagnosis if the FINDINGS
    (dictation) explicitly suggests one. Do not restate every normal field. If nothing
    abnormal was dictated, keep the template's normal IMPRESSION statement (resolving any
    placeholder, e.g. "Normal MRI of the right shoulder.").
    - A recommendation/follow-up instruction dictated alongside a finding (e.g. "advise MRI
      for further evaluation", "clinical correlation advised") is impression-level content —
      state it in IMPRESSION, not inside the FINDINGS field for that finding.
    - Even when abnormal findings are added, it's common to keep a final residual negative
      line adapted from the template's own default IMPRESSION (e.g. "No acute fracture or
      dislocation.", "No acute cardiopulmonary abnormality.") as the last bullet, scoped to
      what's still true (an incidental non-acute lesion doesn't preclude "no acute fracture").
      Only drop it if the dictation's own summary already covers the same ground or the
      finding contradicts it.
11. **Garbled/misspelled dictation:** dictation is raw transcription and may contain typos
    ("effusuon"), shorthand, or fragments ("no effusion, infiltrates"). Interpret intent
    conservatively and route to the right field; do not copy typos into the report.
12. **modality / body_part / study_description / patient_age_band / patient_sex are context
    only.** Use them only to disambiguate pronouns/laterality/anatomy — never invent findings
    from them (e.g. don't add age-related degenerative findings just because the patient is
    elderly, unless dictation or template says so).

## Worked example

Dictation: `mild right basilar opacity, small right pleural effusion`

Template:
```
FINDINGS:
SUPPORT DEVICES: None.
CARDIOMEDIASTINAL SILHOUETTE: Within normal size limits.
LUNGS: No focal airspace opacity or pulmonary edema.
PLEURA: No pleural effusion or pneumothorax.
OSSEOUS STRUCTURES: No acute osseous abnormality identified on these views.
IMPRESSION:
No acute cardiopulmonary abnormality.
```

Expected:
```
FINDINGS:
SUPPORT DEVICES: None.
CARDIOMEDIASTINAL SILHOUETTE: Within normal size limits.
LUNGS: Mild right basilar airspace opacity. No pulmonary edema.
PLEURA: Small right pleural effusion. No pneumothorax.
OSSEOUS STRUCTURES: No acute osseous abnormality identified on these views.
IMPRESSION:
Mild right basilar airspace opacity and small right pleural effusion.
```

Note the opacity is not placed under PLEURA and the effusion is not placed under LUNGS —
field routing by anatomy, not dictation order, is the whole game.

## Self-check before finalizing a case

- [ ] Every FINDINGS field label from `template_content` is present, same order.
- [ ] Every dictated finding appears under the anatomically correct field (or OTHER FINDINGS
      if no better field exists).
- [ ] Untouched fields are byte-for-byte the template wording.
- [ ] No placeholder brackets remain.
- [ ] No fact appears that isn't traceable to dictation or template.
- [ ] Negation/laterality/measurements match the dictation exactly.
- [ ] IMPRESSION reflects only the abnormal findings (or the template's normal line if
      nothing abnormal was dictated), with no new information.
