# SOAP Note Prompting Skill

## Purpose

Define a generate → evaluate workflow for SOAP note generation from therapist audio transcriptions.

This skill separates two artifacts:

- Generation Prompt Guidelines — instruct the LLM how to write the SOAP note
- Evaluation Rubric — score the note quality, fidelity, and compliance

## When to Use

- Generating SOAP notes from a therapist transcript
- Evaluating the quality of a generated SOAP note
- Comparing prompt variants or few-shot strategies
- Checking source fidelity and clinician-review readiness

## Inputs

- Cleaned transcript text
- Optional session metadata, if present
- Optional prompt variant name
- Optional evaluation criteria / threshold

## Prompting Principles

1. Only document information explicitly stated in the transcription.
2. Do not infer appearance, affect, or body language unless the therapist explicitly stated it.
3. Flag missing required elements instead of fabricating them.
4. Use professional clinical language and third-person attribution.
5. Distinguish client-reported information from therapist-observed information.
6. Require clinician review for safety, diagnosis, and risk content.
7. Use separate generation and evaluation calls to avoid self-grading bias.

## Generation Prompt Guidelines

### System Prompt

Use a system prompt that frames the model as a clinical documentation assistant for mental health sessions.

Required behaviors:

- Produce SOAP notes from therapist audio transcription only
- Stay objective, evidence-based, and clinically professional
- Avoid speculation and ungrounded inference
- Preserve HIPAA-safe language and avoid unnecessary PHI
- Follow DSM-5-TR / ICD-11 framing when diagnosis is stated

### User Prompt

Use a user prompt that defines the SOAP sections and what belongs in each section.

#### Subjective

Include only client-reported information:

- Chief complaint or presenting concern
- Self-reported mood, emotions, symptoms
- Stressors, life events, and relevant context
- Sleep, appetite, energy, concentration changes if mentioned
- Medication adherence or side effects if discussed
- Substance use updates if discussed
- SI/HI status, or a missing-data flag if not stated

Use phrases like:

- Client reports
- Client states
- Client described

Use direct quotes when significant.

#### Objective

Include only therapist-observed or explicitly stated facts:

- Appearance, behavior, eye contact, engagement, speech
- Affect as described by the therapist
- Mental status observations only if explicitly stated
- Session logistics: type, duration, modality, participants
- Standardized measure scores if mentioned

If no objective observations were stated, explicitly note that mental status findings were not documented.

#### Assessment

Include the therapist’s clinical interpretation:

- Diagnosis with ICD-10 code if stated, otherwise flag for review
- Progress toward treatment goals
- Risk level or a missing-risk flag
- Clinical impression or case conceptualization
- Barriers to treatment, if mentioned

Risk documentation is mandatory. If risk level is absent, flag for clinician review.

#### Plan

Include next steps as described by the therapist:

- Interventions used in session, including modality and technique
- Client response to interventions
- Homework or between-session tasks
- Next appointment or follow-up plan
- Referrals and care coordination
- Safety planning, if applicable

### Formatting Requirements

- Use clear section headers: SUBJECTIVE, OBJECTIVE, ASSESSMENT, PLAN
- Keep length proportional to session complexity
- Use bullet points for readability when useful
- Include session date, duration, and modality if available
- End with a clinician-review warning line

### Prohibited Actions

- Do not invent observations not stated in the transcript
- Do not add filler or irrelevant content
- Do not use a real client name
- Do not omit safety or risk information
- Do not state a diagnosis without basis in the transcript

## Evaluation Rubric

This rubric is intended for automated or semi-automated evaluation of the
generated SOAP note.

### Scoring Scale

- 0 = Missing/Not Present
- 1 = Present but Deficient (incomplete, vague, or incorrect)
- 2 = Adequate (meets minimum standard)
- 3 = Excellent (thorough, precise, professionally written)
- N/A = Not applicable / Not in source transcription

### SECTION 1: SUBJECTIVE (Max: 21 points)

| Criterion                                                           | Score (0-3) | Notes |
| ------------------------------------------------------------------- | ----------- | ----- |
| 1.1 Chief complaint/presenting concern documented                   |             |       |
| 1.2 Client's self-reported mood/emotional state included            |             |       |
| 1.3 Relevant symptoms documented with frequency/intensity if stated |             |       |
| 1.4 Life stressors or events discussed are captured                 |             |       |
| 1.5 Safety screening (SI/HI) addressed or flagged as missing        |             |       |
| 1.6 Direct quotes used appropriately for significant statements     |             |       |
| 1.7 Proper attribution language ("Client reports/states")           |             |       |

**Section 1 Total: \_\_\_ / 21**

### SECTION 2: OBJECTIVE (Max: 18 points)

| Criterion                                                                      | Score (0-3) | Notes |
| ------------------------------------------------------------------------------ | ----------- | ----- |
| 2.1 Session logistics documented (type, duration, modality)                    |             |       |
| 2.2 Observations accurately reflect what therapist stated (no fabrication)     |             |       |
| 2.3 Mental status elements included OR appropriately flagged as not documented |             |       |
| 2.4 Standardized measures included with scores (if mentioned in source)        |             |       |
| 2.5 Affect/behavior descriptions use clinical terminology                      |             |       |
| 2.6 Clear distinction between observed vs. reported information                |             |       |

**Section 2 Total: \_\_\_ / 18**

### SECTION 3: ASSESSMENT (Max: 18 points)

| Criterion                                                       | Score (0-3) | Notes |
| --------------------------------------------------------------- | ----------- | ----- |
| 3.1 Diagnosis documented with ICD-10 code OR flagged for review |             |       |
| 3.2 Progress toward treatment goals addressed                   |             |       |
| 3.3 Risk level clearly stated OR flagged as missing             |             |       |
| 3.4 Clinical reasoning/case conceptualization reflected         |             |       |
| 3.5 Medical necessity language supports continued treatment     |             |       |
| 3.6 Assessment logically follows from S and O sections          |             |       |

**Section 3 Total: \_\_\_ / 18**

### SECTION 4: PLAN (Max: 18 points)

| Criterion                                                          | Score (0-3) | Notes |
| ------------------------------------------------------------------ | ----------- | ----- |
| 4.1 Specific interventions documented (modality + technique named) |             |       |
| 4.2 Client response to interventions noted                         |             |       |
| 4.3 Homework/between-session tasks documented (if assigned)        |             |       |
| 4.4 Next appointment or follow-up plan stated                      |             |       |
| 4.5 Referrals or care coordination noted (if applicable)           |             |       |
| 4.6 Plan is actionable and specific                                |             |       |

**Section 4 Total: \_\_\_ / 18**

### SECTION 5: COMPLIANCE & SAFETY (Max: 15 points — Critical)

| Criterion                                                              | Score (0-3) | Notes |
| ---------------------------------------------------------------------- | ----------- | ----- |
| 5.1 Risk assessment present or explicitly flagged for clinician review |             |       |
| 5.2 No fabricated clinical observations (hallucination check)          |             |       |
| 5.3 No identifying information beyond "[CLIENT NAME]" placeholder      |             |       |
| 5.4 Diagnosis code accuracy (if ICD-10 stated, is it valid?)           |             |       |
| 5.5 Clinician review flag present at end of note                       |             |       |

**Section 5 Total: \_\_\_ / 15**

### SECTION 6: PROFESSIONAL QUALITY (Max: 15 points)

| Criterion                                                       | Score (0-3) | Notes |
| --------------------------------------------------------------- | ----------- | ----- |
| 6.1 Professional, clinical tone throughout                      |             |       |
| 6.2 Objective language (no judgmental or casual phrasing)       |             |       |
| 6.3 Appropriate length (200-500 words, proportional to session) |             |       |
| 6.4 Proper structure with clear section headers                 |             |       |
| 6.5 Consistent terminology aligned with clinical standards      |             |       |

**Section 6 Total: \_\_\_ / 15**

## TOTAL SCORE: \_\_\_ / 105

### Grade Thresholds

- 95-105: Excellent — Ready for clinician signature with minimal edits
- 84-94: Good — Minor revisions needed
- 73-83: Adequate — Requires clinician additions/corrections
- Below 73: Deficient — Significant revision required

### RED FLAGS (Automatic Failure Conditions)

Check each item. Any "Yes" = Note requires regeneration or major revision:

| Red Flag                                                     | Yes/No |
| ------------------------------------------------------------ | ------ |
| Contains fabricated observations not in source transcription |        |
| Missing safety/risk assessment without flagging for review   |        |
| Contains real client name or identifying PHI                 |        |
| Diagnosis stated without basis in transcription              |        |
| Copy-paste artifacts or irrelevant filler content            |        |
| Tone is unprofessional, casual, or judgmental                |        |
| Significant clinical information from transcription omitted  |        |

### Source Fidelity Check

Evaluate whether the SOAP note matches the transcription:

- All key clinical content from the transcript is represented
- No unsupported information was added
- Direct quotes match the source exactly when used
- Missing information is flagged rather than fabricated
- Therapist observations are not embellished
- Client statements are not misleadingly paraphrased

## Implementation Notes

- Use separate generation and evaluation LLM calls.
- Prefer a different model for evaluation when possible.
- Return structured JSON from evaluation for automated scoring.
- Always require human sign-off for final medical documentation.

## TODO

- [ ] Replace placeholder text with final prompt variants
- [ ] Add JSON schema for evaluation output
- [ ] Add example transcript-to-note test cases
- [ ] Add a source-fidelity regression checklist
