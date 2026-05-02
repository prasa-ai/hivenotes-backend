For an LLM pipeline to generate SOAP notes (generate → evaluate), it needs two distinct artifacts:
Generation Prompt Guidelines — What to instruct the LLM to do

Evaluation Rubric — Scorable criteria to assess output quality

Here's the critical issue with using my original checklist as-is:

The LLM can only document what the therapist verbally reported. It can't observe appearance, affect, or body language unless the therapist explicitly stated it. So your evaluation criteria must account for "information present in source transcription" vs. "information missing but required."

Part 1: Generation Prompt Guidelines
This goes into your system prompt when generating SOAP notes

## SYSTEM PROMPT

# SOAP Note Generation Guidelines for Mental Health Sessions

You are a clinical documentation assistant generating SOAP notes from a therapist's audio transcription of a session. Follow these guidelines precisely.

## USER PROMPT

## Core Principles

1. **Only document information explicitly stated in the transcription** — never infer or fabricate clinical observations
2. **Flag missing required elements** — if critical information (e.g., risk assessment) was not mentioned, note: "[NOT DOCUMENTED IN SESSION RECORDING]"
3. **Use professional clinical language** — third person, objective, non-judgmental
4. **Attribute appropriately** — distinguish between what the client reported vs. what the therapist observed

---

## SUBJECTIVE Section

Document client-reported information:

- Chief complaint or presenting concern (use client's words when available)
- Self-reported mood, emotions, symptoms
- Relevant life events or stressors discussed
- Sleep, appetite, energy, concentration changes (if mentioned)
- Medication compliance and side effects (if discussed)
- Substance use updates (if discussed)
- Suicidal/homicidal ideation status (REQUIRED — if not mentioned, flag as missing)
  **Language pattern:** "Client reports...", "Client states...", "Client described..."
  **Use direct quotes** for significant statements when available in transcription.

---

## OBJECTIVE Section

Document therapist's observations as reported in the recording:

- Appearance (only if therapist described it)
- Behavior, eye contact, engagement (only if therapist described it)
- Speech characteristics (only if therapist noted)
- Affect as observed by therapist (only if described)
- Mental status observations (only what was explicitly stated)
- Session logistics: type, duration, modality, who was present
- Standardized measure scores (PHQ-9, GAD-7, etc.) if mentioned
  **Critical:** If the therapist did not verbally describe observations, state: "Mental status examination findings not documented in session recording." Do NOT fabricate observations.

---

## ASSESSMENT Section

Document clinical interpretation as stated by therapist:

- Diagnosis with ICD-10 code (if stated; otherwise flag as needed)
- Progress toward treatment goals (use therapist's stated assessment)
- Risk level (REQUIRED — must be explicitly documented or flagged as missing)
- Clinical impressions or case conceptualization discussed
- Barriers to treatment (if mentioned)
  **Risk documentation is mandatory.** If therapist did not state risk level, output: "RISK ASSESSMENT: Not documented in session recording — REQUIRES CLINICIAN REVIEW"

---

## PLAN Section

Document next steps as described by therapist:

- Interventions used this session (be specific: name modality and technique)
- Client response to interventions
- Homework or between-session assignments
- Next appointment (date, time, frequency)
- Referrals or coordination of care
- Safety planning (if applicable)

---

## Formatting Requirements

- Use clear section headers: **SUBJECTIVE**, **OBJECTIVE**, **ASSESSMENT**, **PLAN**
- Keep total length between 200-500 words unless session complexity requires more
- Use bullet points within sections for readability
- Include session date, duration, and modality at top
- End with: "[GENERATED FROM AUDIO TRANSCRIPTION — REQUIRES CLINICIAN REVIEW AND SIGNATURE]"

---

## Prohibited Actions

- Do NOT invent observations not stated in transcription
- Do NOT copy-paste filler language unrelated to session content
- Do NOT include personal opinions or judgmental language
- Do NOT use client's real name (use "Client" or "[CLIENT NAME]")
- Do NOT omit safety/risk information — flag if missing
- Do NOT diagnose if therapist did not state diagnosis — flag for review
  Part 2: Evaluation Rubric
  This is what to use to score/evaluate the generated SOAP note

Structure this as JSON-friendly criteria for automated or semi-automated evaluation:

# SOAP Note Evaluation Rubric

## Scoring Scale

- 0 = Missing/Not Present
- 1 = Present but Deficient (incomplete, vague, or incorrect)
- 2 = Adequate (meets minimum standard)
- 3 = Excellent (thorough, precise, professionally written)
- N/A = Not applicable / Not in source transcription

---

## SECTION 1: SUBJECTIVE (Max: 21 points)

| Criterion                                                           | Score (0-3) | Notes |
| ------------------------------------------------------------------- | ----------- | ----- |
| 1.1 Chief complaint/presenting concern documented                   |             |       |
| 1.2 Client's self-reported mood/emotional state included            |             |       |
| 1.3 Relevant symptoms documented with frequency/intensity if stated |             |       |
| 1.4 Life stressors or events discussed are captured                 |             |       |
| 1.5 Safety screening (SI/HI) addressed or flagged as missing        |             |       |
| 1.6 Direct quotes used appropriately for significant statements     |             |       |
| 1.7 Proper attribution language ("Client reports/states")           |             |       |

## **Section 1 Total: \_\_\_ / 21**

## SECTION 2: OBJECTIVE (Max: 18 points)

| Criterion                                                                      | Score (0-3) | Notes |
| ------------------------------------------------------------------------------ | ----------- | ----- |
| 2.1 Session logistics documented (type, duration, modality)                    |             |       |
| 2.2 Observations accurately reflect what therapist stated (no fabrication)     |             |       |
| 2.3 Mental status elements included OR appropriately flagged as not documented |             |       |
| 2.4 Standardized measures included with scores (if mentioned in source)        |             |       |
| 2.5 Affect/behavior descriptions use clinical terminology                      |             |       |
| 2.6 Clear distinction between observed vs. reported information                |             |       |

## **Section 2 Total: \_\_\_ / 18**

## SECTION 3: ASSESSMENT (Max: 18 points)

| Criterion                                                       | Score (0-3) | Notes |
| --------------------------------------------------------------- | ----------- | ----- |
| 3.1 Diagnosis documented with ICD-10 code OR flagged for review |             |       |
| 3.2 Progress toward treatment goals addressed                   |             |       |
| 3.3 Risk level clearly stated OR flagged as missing             |             |       |
| 3.4 Clinical reasoning/case conceptualization reflected         |             |       |
| 3.5 Medical necessity language supports continued treatment     |             |       |
| 3.6 Assessment logically follows from S and O sections          |             |       |

## **Section 3 Total: \_\_\_ / 18**

## SECTION 4: PLAN (Max: 18 points)

| Criterion                                                          | Score (0-3) | Notes |
| ------------------------------------------------------------------ | ----------- | ----- |
| 4.1 Specific interventions documented (modality + technique named) |             |       |
| 4.2 Client response to interventions noted                         |             |       |
| 4.3 Homework/between-session tasks documented (if assigned)        |             |       |
| 4.4 Next appointment or follow-up plan stated                      |             |       |
| 4.5 Referrals or care coordination noted (if applicable)           |             |       |
| 4.6 Plan is actionable and specific                                |             |       |

## **Section 4 Total: \_\_\_ / 18**

## SECTION 5: COMPLIANCE & SAFETY (Max: 15 points — Critical)

| Criterion                                                              | Score (0-3) | Notes |
| ---------------------------------------------------------------------- | ----------- | ----- |
| 5.1 Risk assessment present or explicitly flagged for clinician review |             |       |
| 5.2 No fabricated clinical observations (hallucination check)          |             |       |
| 5.3 No identifying information beyond "[CLIENT NAME]" placeholder      |             |       |
| 5.4 Diagnosis code accuracy (if ICD-10 stated, is it valid?)           |             |       |
| 5.5 Clinician review flag present at end of note                       |             |       |

## **Section 5 Total: \_\_\_ / 15**

## SECTION 6: PROFESSIONAL QUALITY (Max: 15 points)

| Criterion                                                       | Score (0-3) | Notes |
| --------------------------------------------------------------- | ----------- | ----- |
| 6.1 Professional, clinical tone throughout                      |             |       |
| 6.2 Objective language (no judgmental or casual phrasing)       |             |       |
| 6.3 Appropriate length (200-500 words, proportional to session) |             |       |
| 6.4 Proper structure with clear section headers                 |             |       |
| 6.5 Consistent terminology aligned with clinical standards      |             |       |

## **Section 6 Total: \_\_\_ / 15**

## TOTAL SCORE: \_\_\_ / 105

### Grade Thresholds

- 95-105: Excellent — Ready for clinician signature with minimal edits
- 84-94: Good — Minor revisions needed
- 73-83: Adequate — Requires clinician additions/corrections
- Below 73: Deficient — Significant revision required

---

## RED FLAGS (Automatic Failure Conditions)

Check each item. Any "Yes" = Note requires regeneration or major revision:
| Red Flag | Yes/No |
|----------|--------|
| Contains fabricated observations not in source transcription | |
| Missing safety/risk assessment without flagging for review | |
| Contains real client name or identifying PHI | |
| Diagnosis stated without basis in transcription | |
| Copy-paste artifacts or irrelevant filler content | |
| Tone is unprofessional, casual, or judgmental | |
| Significant clinical information from transcription omitted | |
Part 3: Source Fidelity Check
This is unique to this LLM use case — evaluate whether the output matches the input transcription

# Source Fidelity Evaluation

## Transcription → SOAP Alignment Check

| Check                                                           | Pass/Fail | Notes |
| --------------------------------------------------------------- | --------- | ----- |
| All key clinical content from transcription is represented      |           |       |
| No information was added that wasn't in transcription           |           |       |
| Direct quotes match source transcription exactly                |           |       |
| Missing information appropriately flagged (not fabricated)      |           |       |
| Therapist's stated observations not embellished                 |           |       |
| Client's reported statements not paraphrased in misleading ways |           |       |

## Information Classification

Review each element in the generated note:
| Information Type | Expected Handling |
|------------------|-------------------|
| Explicitly stated in transcription | Include as documented |
| Implied but not stated | Do NOT include (or flag with uncertainty) |
| Required but missing from transcription | Flag: "[NOT DOCUMENTED — REQUIRES CLINICIAN INPUT]" |
| Contradictory information in transcription | Flag for clinician review |
Implementation Recommendations
For your Vibe pipeline:

┌─────────────────────┐
│ Audio Recording │
└──────────┬──────────┘
│
▼
┌─────────────────────┐
│ Transcription │
│ (Whisper/etc.) │
└──────────┬──────────┘
│
▼
┌─────────────────────┐
│ LLM Generation │◄── Generation Prompt Guidelines
│ (SOAP Note) │
└──────────┬──────────┘
│
▼
┌─────────────────────┐
│ LLM Evaluation │◄── Evaluation Rubric
│ (Quality Score) │◄── Source Fidelity Check
└──────────┬──────────┘
│
▼
┌─────────────────────┐
│ Clinician Review │
│ (Final Approval) │
└─────────────────────┘
Key architecture decisions:

Use separate LLM calls for generation and evaluation — avoids self-grading bias

Consider using a different model for evaluation — e.g., generate with Sonnet, evaluate with Opus

Return structured JSON from evaluation — makes it easy to flag specific issues to the clinician

Always require human sign-off — this is medical documentation; LLM is assistance, not replacement
