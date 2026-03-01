# KOL Voice Orchestrator（X 風格萃取 + 生成 + 去 AI 味）Skill Design Spec

- Owner: Huang Chihan
- Status: Draft (Implementation-ready)
- Last Updated: 2026-03-02 (Asia/Taipei)
- Scope: MVP focuses on X (Twitter) post style extraction + post generation (manual publish)
- Nature: **Pure AI skill orchestration** — no traditional programming; skills are prompt-driven and use MCP tools
- Non-goals (MVP): auto-publish, multi-account workspace, analytics attribution

---

## 0) High-level Summary

This project is an **orchestrator** composed of 4 sub-skills:

- **Skill A (Ingest)**: Build a corpus of ~500 posts from a target X account **via `chrome-dev-mcp`** (browser-based scraping) or user upload. Store as artifact.
- **Skill B (Voice Profile Builder)**: Analyze corpus and output a **machine-consumable “Voice Profile”** (style mechanics + constraints).
- **Skill C (Draft Generator)**: Generate a post draft from a user topic + voice profile (X format constraints).
- **Skill D (Humanize / De-AI + Lint)**: Rewrite draft to reduce AI feel while preserving meaning; enforce lints and similarity constraints.

Key principle: **Separate facts from style** (generate content skeleton first, then rewrite in style). This materially reduces “AI smell”.

---

## 1) Constraints (MUST)

### 1.1 Ingest via `chrome-dev-mcp`
- Skill A uses `chrome-dev-mcp` to navigate the target KOL's X profile page in a real Chrome browser and extract post content from the DOM.
- This is a browser-automation approach via MCP tools — the AI skill drives Chrome DevTools to scroll, read, and collect tweets.
- Respect reasonable pacing: add delays between scroll actions to avoid triggering X's client-side rate limiting.

### 1.2 Avoid impersonation / misleading identity
- The output must be “inspired by extracted style mechanics”, not “as the influencer”.
- Do not include claims implying the influencer authored the post.
- Avoid verbatim catchphrases that are uniquely identifying.

Reference:
- X authenticity: https://help.x.com/en/rules-and-policies/authenticity

### 1.3 X character counting
- Posts on X can contain up to **280 characters**, and X has special counting rules for emojis/URLs/Unicode. citeturn0search1
- Implement conservative guardrail: keep drafts <= **240 chars** (safe buffer), or implement exact X counting logic.

Reference:
- Counting Characters: citeturn0search1

---

## 2) System Architecture & Repo Layout (Suggested)

```text
kol-voice-orchestrator/
  docs/
    kol-voice-orchestrator.md  # this spec
  skills/
    ingest/                    # Skill A (chrome-dev-mcp scraping)
      SKILL.md
    profile/                   # Skill B (voice profile builder)
      SKILL.md
    generate/                  # Skill C (draft generator)
      SKILL.md
    humanize/                  # Skill D (de-AI + lint)
      SKILL.md
  schemas/
    voice_profile.schema.json
    draft_package.schema.json
  artifacts/
    kol/<handle>/
      corpus.v1.jsonl
      corpus_meta.json
      voice_profile.v1.json
      drafts/
        2026-03-02.topic-hash/
          draft_package.json
          preview.md
```

This is a **pure AI skill project** — each skill is a prompt-driven SKILL.md with instructions for the AI agent. No traditional programming is involved.

---

## 3) Orchestrator Flow

The orchestrator is an AI agent that drives each skill sequentially:

1.  **Skill A (Ingest)**: Use `chrome-dev-mcp` to open the KOL's X profile, scroll and collect ~500 posts → save `corpus.v1.jsonl` + `corpus_meta.json`
2.  **Skill B (Voice Profile)**: Analyze corpus → save `voice_profile.v1.json`
3.  **Skill C (Draft Generator)**: Given topic + voice profile + **few-shot corpus examples** → produce `draft_v0`
4.  **Skill D (Humanize + Lint)**: Rewrite `draft_v0` → `draft_v1_humanized` + `lint_report` (includes perplexity/burstiness checks + human imperfection injection)
5.  **Skill D.2 (Self-Evaluation)**: AI self-critiques `draft_v1_humanized` — if it still smells like AI, loop back to step 4 (max 2 iterations)
6.  **Output**: Write `draft_package.json` + `preview.md`

User can also provide a corpus via file upload (import mode) instead of running Skill A.

### 3.1 Input Validation (must)
Before executing any skill, the orchestrator must check for required inputs. If anything is missing, **ask the user** instead of failing:

| Input | Required for | If missing |
|---|---|---|
| `handle` | Skill A, B | Ask: "請提供目標 KOL 的 X handle" |
| `topic` | Skill C | Ask: "請提供你想寫的主題" |
| `facts` | Skill C (optional) | Ask: "有沒有具體事實要放進推文？沒有的話我會根據主題生成" |
| `lang` | Skill C | Auto-infer from `voice_profile.language_mix`, or ask user |
| `corpus` | Skill B | Auto-trigger Skill A, or ask: "要我從 X 上收集推文嗎？" |
| `voice_profile` | Skill C, D | Auto-trigger Skill B |

---

## 4) Skill A — Ingest (via `chrome-dev-mcp`)

### 4.1 Modes

**Mode A: Browser scraping via `chrome-dev-mcp` (primary)**
- Use `chrome-dev-mcp` tools to navigate to `https://x.com/<handle>` in a real Chrome browser.
- Scroll the timeline repeatedly to load posts; extract post text, timestamps, and engagement metrics from the DOM.
- Collect up to ~500 posts per session.
- Add reasonable delays between scroll actions (e.g., 1–2 seconds) to avoid client-side throttling.
- Parse each tweet element to extract: post text, timestamp, like/reply/repost counts, hashtags, mentions, and URLs.

**Mode B: Upload/import (fallback)**
- Accept JSONL/CSV/plain text exported by the user.
- Normalize into the same corpus format as browser-scraped mode.

### 4.2 Pacing & Robustness
- Add delays between scroll actions to mimic human browsing.
- Handle dynamic loading: wait for new tweet elements to appear after each scroll.
- Store partial progress periodically so that interrupted sessions can be resumed.

### 4.3 Caching & Incremental Updates
- Store `corpus.vN.jsonl` and `corpus_meta.json` (`last_seen_post_id`, `fetched_at`).
- Prefer incremental updates (fetch new posts since `last_seen_post_id`) instead of re-scraping all 500 each time.

---

## 5) Corpus Artifact Format (tweets corpus)

### 5.1 Normalized JSONL record
Each line is a JSON object:

```json
{
  "id": "1890123456789012345",
  "created_at": "2026-02-28T12:34:56Z",
  "text_raw": "post text ...",
  "text_norm": "post text with <URL> <MENTION> ...",
  "lang": "en",
  "entities": {
    "hashtags": ["buildinpublic"],
    "mentions": ["someone"],
    "urls": ["https://t.co/..."]
  },
  "public_metrics": {
    "like_count": 10,
    "reply_count": 2,
    "repost_count": 1,
    "quote_count": 0
  },
  "source": {
    "platform": "x",
    "handle": "somekol",
    "via": "x_api_v2|upload",
    "fetched_at": "2026-03-02T10:00:00+08:00"
  }
}
```

### 5.2 Corpus metadata file
`corpus_meta.json`

```json
{
  "handle": "somekol",
  "count": 500,
  "via": "x_api_v2",
  "built_at": "2026-03-02T10:00:00+08:00",
  "pagination": {
    "last_seen_post_id": "1890...",
    "next_token": "..."
  }
}
```

---

## 6) Normalize Rules (MUST)

Goal: make downstream analysis stable and prevent leakage/impersonation risks.

### 6.1 Text cleaning
- Preserve original text for overlap checks (`text_raw`).
- Create `text_norm` for analysis:
  - Replace URLs with `<URL>`
  - Replace @mentions with `<MENTION>`
  - Replace hashtags with `<HASHTAG:tag>` (or keep raw hashtags; pick one and keep consistent)
  - Collapse multiple spaces
  - Keep line breaks (they are style signal)

### 6.2 Language handling
- Keep all languages but compute mix ratios.
- For MVP, analyze the dominant language and treat others as auxiliary signals.

### 6.3 Remove non-content posts (optional)
- Drop posts with only media/URL and no textual signal (threshold: >= 15 non-token chars)

---

## 7) Skill B — Voice Profile Builder

### 7.1 Output: voice_profile.json (machine-consumable)
This is the contract consumed by Skill C and Skill D.

#### Example structure
```json
{
  "profile_meta": {
    "handle": "somekol",
    "version": "v1",
    "built_at": "2026-03-02T10:20:00+08:00",
    "corpus_size": 500,
    "language_mix": {"en": 0.75, "zh": 0.20, "other": 0.05}
  },
  "style_mechanics": {
    "formatting": {
      "avg_chars": 180,
      "p50_chars": 160,
      "p90_chars": 240,
      "line_break_rate": 0.35,
      "emoji_rate": 0.25,
      "emoji_placement": "end|inline|both",
      "punctuation_signals": {
        "dash_rate": 0.10,
        "colon_rate": 0.18,
        "question_rate": 0.12
      },
      "hashtag_rules": {
        "count_range": [0, 2],
        "position": "end",
        "topic_tags": ["buildinpublic", "startup"]
      }
    },
    "rhetorical_moves": {
      "opening_templates": [
        "Hot take: {claim}",
        "{Outcome}. Here's what changed:",
        "I used to think {old}, but {new}"
      ],
      "common_transitions": [
        "Here's the thing—",
        "So I tried…",
        "What surprised me:"
      ],
      "cta_patterns": [
        "If you're building {x}, try {y}.",
        "Curious if others see this too."
      ]
    },
    "lexicon": {
      "preferred_verbs": ["ship", "cut", "simplify", "learn"],
      "banned_hype_words": ["revolutionary", "game-changer", "insane"],
      "signature_phrases": [
        {"text": "…", "uniqueness": "high", "policy": "avoid_verbatim"}
      ]
    }
  },
  "constraints": {
    "no_impersonation": true,
    "no_verbatim_copy": true,
    "max_ngram_overlap": 0.12,
    "max_semantic_similarity": 0.92
  }
}
```

### 7.2 Analysis Implementation (recommended approach)
Use deterministic heuristics first; optionally layer LLM for:
- proposing opening templates (from clustered openings)
- generating banned filler list (AI-ish phrases)
- summarizing “do/don’t rules” (but keep the JSON fields deterministic)

Deterministic metrics to compute:
- char distribution (p50/p90)
- avg lines per post (line breaks)
- emoji placement distribution (end vs inline)
- top opening bigrams/trigrams
- transition markers frequency
- hashtag count distribution

---

## 8) Skill C — Draft Generator (Topic -> Draft v0)

### 8.1 Inputs (contract)
```json
{
  "topic": "Explain why I switched to edge DB for faster UX",
  "facts": [
    "Moved database closer to users",
    "Reduced request latency (no fabricated numbers)",
    "Simplified migration process"
  ],
  "target_platform": "x",
  "lang": "en|zh|both",
  "voice_profile": { "...": "..." },
  "few_shot_examples": [
    {"text_norm": "...", "like_count": 120},
    {"text_norm": "...", "like_count": 95},
    {"text_norm": "...", "like_count": 88}
  ]
}
```

### 8.2 Few-shot example selection (must)
- Include **3–5 real corpus posts** as style reference in every Skill C prompt.
- Select posts ranked by **engagement** (top by like + repost count) — these represent the KOL's most resonant voice.
- These examples are for style reference only; the prompt must instruct the LLM **NOT to copy content** from them.
- Prompt framing:
  ```
  Here are real posts from this author (for style reference only, do NOT copy):
  1. "{post_1}"
  2. "{post_2}"
  3. "{post_3}"

  Voice profile: {voice_profile_json}
  Topic: {topic}
  Facts: {facts}

  Generate a draft inspired by the style mechanics above.
  ```

### 8.3 Outputs (contract)
```json
{
  "draft_v0": "text...",
  "char_count_estimate": 210,
  "notes": ["used opening_template #2", "kept 1 hashtag"]
}
```

### 8.4 Constraints (must enforce)
- Do not imply influencer identity
- No fabricated metrics
- Target <= 240 chars (buffer), or implement exact X counting rules (recommended later)
- Keep hashtags within `voice_profile.style_mechanics.formatting.hashtag_rules`

---

## 9) Skill D — Humanize / De-AI + Lint (Draft v0 -> v1)

### 9.1 Two-pass rule (must)
- Pass 1 (Skill C): guarantee correctness of the content skeleton.
- Pass 2 (Skill D): rewrite only style, preserve meaning:
  - remove AI filler (“In today’s world…”, “It is important to note…”, “Delve into…”)
  - avoid “too balanced” sentences; allow short punchy lines
  - apply voice formatting rules (line breaks, transitions, emoji placement)
  - apply char-length compression if > 240 chars
  - **inject human imperfections** (see 9.5)
- After rewrite, re-run overlap checks, safety checks, **and perplexity/burstiness checks** (see 9.6).

### 9.2 Inputs (contract)
```json
{
  "draft_v0": "text...",
  "target_platform": "x",
  "lang": "en",
  "voice_profile": { "...": "..." },
  "lint_policy": {
    "max_chars": 240,
    "ai_phrase_blacklist": ["In today's world", "It is important to note"],
    "require_human_variance": true
  }
}
```

### 9.3 Outputs (contract)
```json
{
  "draft_v1_humanized": "text...",
  "lint_report": {
    "char_count": 232,
    "perplexity_estimate": "medium",
    "burstiness_estimate": "good",
    "issues": [
      {"type": "ai_phrase", "span": "It is important to note", "action": "removed"},
      {"type": "hype_without_evidence", "span": "massive", "action": "softened"},
      {"type": "overlap", "value": 0.10, "threshold": 0.12, "action": "ok"},
      {"type": "burstiness", "status": "ok", "action": "none"},
      {"type": "perplexity", "status": "ok", "action": "none"}
    ]
  },
  "self_eval": {
    "ai_smell_score": 3,
    "pass": true,
    "notes": ["varied sentence lengths", "no AI filler detected"],
    "iteration": 1
  }
}
```

### 9.4 Lint rules (must)
- AI filler phrases: remove/replace
- hype words (global + profile banned list): soften
- length: <= max chars
- overlap: n-gram overlap check against corpus
- uniqueness: avoid signature phrases verbatim if policy is `avoid_verbatim`
- **perplexity**: flag if word choices are too predictable/uniform (see 9.6)
- **burstiness**: flag if sentence lengths are too uniform (see 9.6)

### 9.5 Human Imperfection Injection (must)
AI text is often flagged because it is too perfect. Skill D must deliberately introduce controlled human imperfections, guided by the voice profile:

- **Contractions**: Use "don't", "can't", "it's" instead of "do not", "cannot", "it is" (match KOL's contraction rate)
- **Sentence fragments**: Allow punchy fragments ("Shipped it. Finally.", "Not even close.")
- **Informal punctuation**: Apply em-dashes, ellipses, loose comma usage per KOL's patterns
- **Varied line breaks**: Mix single-line statements with multi-line blocks; avoid uniform paragraph structure
- **Colloquialisms**: Use casual phrasing where the KOL's voice supports it

The key principle: **match the KOL's natural imperfection patterns** from the corpus, don't just add random errors.

### 9.6 Perplexity & Burstiness Checks (must)
AI detectors primarily use two metrics to flag AI-generated text:

- **Perplexity** (word predictability): AI text uses statistically likely words, yielding low perplexity. Human text has higher perplexity due to unexpected word choices.
- **Burstiness** (sentence length variation): AI text has uniform sentence lengths. Human text mixes short punchy lines with longer complex ones.

After humanization, Skill D must evaluate the draft:
- If **perplexity is too low** (words too predictable): swap some words for less obvious synonyms, add rhetorical questions, use unexpected transitions
- If **burstiness is too uniform** (sentences all same length): break long sentences into fragments, combine short ones, vary rhythm
- Report status in `lint_report` as `perplexity_estimate` and `burstiness_estimate`

### 9.7 Self-Evaluation Loop (must)
After producing `draft_v1_humanized`, Skill D must run a **self-critique step**:

1. Evaluate: "Does this text sound like it was written by an AI? Rate 1-10 (1 = fully human, 10 = obvious AI)."
2. Evaluate: "Would this pass as a genuine post from someone with this voice profile?"
3. Evaluate: "Are there any phrases that feel generic, filler-like, or AI-generated?"

Decision logic:
- If `ai_smell_score` <= 4: **pass**, finalize draft
- If `ai_smell_score` > 4: **rewrite** with specific fixes from self-eval notes, then re-evaluate
- **Max 2 iterations** to avoid infinite loops

Output in `self_eval`:
```json
{
  "ai_smell_score": 3,
  "pass": true,
  "notes": ["varied sentence lengths", "no AI filler detected"],
  "iteration": 1
}
```

---

## 10) Similarity / Overlap Check (Anti-verbatim)

### 10.1 N-gram overlap (cheap & effective)
- Use `text_norm`
- Compute overlap between draft and each corpus post:
  - shingles of 3-grams or 4-grams
  - Jaccard similarity
- Must satisfy: `max_ngram_overlap <= voice_profile.constraints.max_ngram_overlap`

### 10.2 Optional semantic similarity (more expensive)
- Use embeddings + cosine similarity
- Must satisfy `max_semantic_similarity <= threshold` (e.g., 0.92)

---

## 11) Output Package

Write both:
- `draft_package.json` (machine)
- `preview.md` (human)

`preview.md` example:

```md
# Draft Package

## Topic
...

## X (EN)
- v0: ...
- v1 (humanized): ...

## Lint report
- char_count: 232
- issues:
  - removed AI filler: "..."
```

---

## 12) JSON Schemas

### 12.1 `voice_profile.schema.json` (Draft 2020-12, starter)
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.local/schemas/voice_profile.schema.json",
  "type": "object",
  "required": ["profile_meta", "style_mechanics", "constraints"],
  "properties": {
    "profile_meta": {
      "type": "object",
      "required": ["handle", "version", "built_at", "corpus_size", "language_mix"],
      "properties": {
        "handle": {"type": "string", "minLength": 1},
        "version": {"type": "string", "pattern": "^v[0-9]+$"},
        "built_at": {"type": "string", "format": "date-time"},
        "corpus_size": {"type": "integer", "minimum": 10},
        "language_mix": {
          "type": "object",
          "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}
        }
      }
    },
    "style_mechanics": {
      "type": "object",
      "required": ["formatting", "rhetorical_moves", "lexicon"],
      "properties": {
        "formatting": {
          "type": "object",
          "required": ["avg_chars", "p50_chars", "p90_chars", "line_break_rate", "emoji_rate", "hashtag_rules"],
          "properties": {
            "avg_chars": {"type": "integer", "minimum": 1},
            "p50_chars": {"type": "integer", "minimum": 1},
            "p90_chars": {"type": "integer", "minimum": 1},
            "line_break_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "emoji_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "emoji_placement": {"type": "string", "enum": ["end", "inline", "both"]},
            "hashtag_rules": {
              "type": "object",
              "required": ["count_range", "position"],
              "properties": {
                "count_range": {
                  "type": "array",
                  "items": {"type": "integer", "minimum": 0},
                  "minItems": 2,
                  "maxItems": 2
                },
                "position": {"type": "string", "enum": ["end", "none", "any"]},
                "topic_tags": {"type": "array", "items": {"type": "string"}}
              }
            }
          }
        },
        "rhetorical_moves": {
          "type": "object",
          "required": ["opening_templates", "common_transitions", "cta_patterns"],
          "properties": {
            "opening_templates": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "common_transitions": {"type": "array", "items": {"type": "string"}},
            "cta_patterns": {"type": "array", "items": {"type": "string"}}
          }
        },
        "lexicon": {
          "type": "object",
          "required": ["preferred_verbs", "banned_hype_words"],
          "properties": {
            "preferred_verbs": {"type": "array", "items": {"type": "string"}},
            "banned_hype_words": {"type": "array", "items": {"type": "string"}},
            "signature_phrases": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["text", "uniqueness", "policy"],
                "properties": {
                  "text": {"type": "string"},
                  "uniqueness": {"type": "string", "enum": ["low", "medium", "high"]},
                  "policy": {"type": "string", "enum": ["allow", "avoid_verbatim"]}
                }
              }
            }
          }
        }
      }
    },
    "constraints": {
      "type": "object",
      "required": ["no_impersonation", "no_verbatim_copy", "max_ngram_overlap"],
      "properties": {
        "no_impersonation": {"type": "boolean"},
        "no_verbatim_copy": {"type": "boolean"},
        "max_ngram_overlap": {"type": "number", "minimum": 0, "maximum": 1},
        "max_semantic_similarity": {"type": "number", "minimum": 0, "maximum": 1}
      }
    }
  }
}
```

### 12.2 `draft_package.schema.json` (starter)
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["topic", "drafts", "lint", "safety"],
  "properties": {
    "topic": {"type": "string"},
    "language": {"type": "string", "enum": ["en", "zh", "both"]},
    "drafts": {
      "type": "object",
      "properties": {
        "x": {
          "type": "object",
          "required": ["v0", "v1_humanized"],
          "properties": {
            "v0": {"type": "string"},
            "v1_humanized": {"type": "string"}
          }
        }
      },
      "additionalProperties": false
    },
    "lint": {
      "type": "object",
      "required": ["x_char_count", "issues"],
      "properties": {
        "x_char_count": {"type": "integer", "minimum": 0},
        "issues": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["type", "span", "action"],
            "properties": {
              "type": {"type": "string"},
              "span": {"type": "string"},
              "action": {"type": "string"}
            }
          }
        }
      }
    },
    "safety": {
      "type": "object",
      "required": ["warnings", "redactions"],
      "properties": {
        "warnings": {"type": "array", "items": {"type": "string"}},
        "redactions": {"type": "array", "items": {"type": "string"}}
      }
    }
  }
}
```

---

## 13) Prompt Contracts (Skill B/C/D)

> Prompts must enforce: no fabricated metrics, no impersonation, no verbatim copying.
> Use X char guardrails (280 max; implement 240 buffer by default). citeturn0search1

### 13.1 Skill B Prompt Contract (Analyze -> Voice Profile)
- Extract style mechanics from corpus.
- Output valid JSON matching `voice_profile.schema.json`.
- Do NOT output verbatim signature phrases (only list them under `signature_phrases` with `policy=avoid_verbatim`).

**Input JSON**
```json
{
  "handle": "somekol",
  "corpus_sample": [
    {"id":"...", "text_norm":"...", "lang":"en"},
    {"id":"...", "text_norm":"...", "lang":"en"}
  ],
  "stats_hint": {"corpus_size": 500}
}
```

**Output JSON**
- Must match `voice_profile.schema.json`.

### 13.2 Skill C Prompt Contract (Topic -> Draft v0)
- Create an X post draft inspired by voice mechanics.
- Must not impersonate.
- Must not fabricate metrics.
- Target <= 240 chars unless exact counting is implemented.
- **Must include 3-5 few-shot corpus examples** (top-engagement posts) as style reference.
- Prompt must explicitly instruct: "These examples are for style reference only. Do NOT copy content from them."

**Input JSON**
```json
{
  "topic": "....",
  "facts": ["...","..."],
  "lang": "en",
  "target_platform": "x",
  "voice_profile": { "...": "..." },
  "few_shot_examples": [
    {"text_norm": "...", "like_count": 120},
    {"text_norm": "...", "like_count": 95}
  ]
}
```

**Output JSON**
```json
{
  "draft_v0": "....",
  "char_count_estimate": 200,
  "notes": ["..."]
}
```

### 13.3 Skill D Prompt Contract (Draft v0 -> Draft v1 Humanized)
- Rewrite to sound less AI while preserving meaning.
- Apply voice formatting and lexicon preferences.
- Remove banned filler/hype words.
- Enforce overlap constraints.
- **Inject human imperfections** (contractions, fragments, informal punctuation) per voice profile patterns.
- **Evaluate perplexity & burstiness** — flag if too uniform.
- **Run self-evaluation**: rate AI smell 1-10; if > 4, rewrite (max 2 iterations).

**Input JSON**
```json
{
  "draft_v0": "....",
  "lang": "en",
  "target_platform": "x",
  "voice_profile": { "...": "..." },
  "lint_policy": {
    "max_chars": 240,
    "ai_phrase_blacklist": ["In today's world", "It is important to note"]
  }
}
```

**Output JSON**
```json
{
  "draft_v1_humanized": "....",
  "lint_report": {
    "char_count": 232,
    "perplexity_estimate": "medium",
    "burstiness_estimate": "good",
    "issues": [
      {"type":"ai_phrase","span":"...","action":"removed"}
    ]
  },
  "self_eval": {
    "ai_smell_score": 3,
    "pass": true,
    "notes": ["..."],
    "iteration": 1
  }
}
```

---

## 14) References (must-read)
- chrome-dev-mcp: MCP server for Chrome DevTools browser automation
- Counting characters: citeturn0search1
- X authenticity policy: https://help.x.com/en/rules-and-policies/authenticity

---

## 15) Implementation Progress

### Orchestrator
- [x] `skills/orchestrator/SKILL.md` — orchestrator flow + input validation logic

### Skill A — Ingest
- [x] `skills/ingest/SKILL.md` — chrome-dev-mcp scraping instructions
- [x] Corpus JSONL output format (`corpus.v1.jsonl`)
- [x] Corpus metadata output (`corpus_meta.json`)
- [x] Normalize rules (text_raw → text_norm)
- [ ] Upload/import mode (Mode B)
- [x] Incremental update / caching logic

### Skill B — Voice Profile Builder
- [ ] `skills/profile/SKILL.md` — analysis prompt + deterministic metrics
- [ ] Voice profile output (`voice_profile.v1.json`)
- [ ] Engagement-weighted analysis (optional enhancement)

### Skill C — Draft Generator
- [ ] `skills/generate/SKILL.md` — draft generation prompt
- [ ] Few-shot example selection logic (top engagement posts)
- [ ] Input/output contract validation

### Skill D — Humanize / De-AI + Lint
- [ ] `skills/humanize/SKILL.md` — humanization prompt
- [ ] AI filler phrase removal
- [ ] Human imperfection injection (§9.5)
- [ ] Perplexity & burstiness checks (§9.6)
- [ ] Self-evaluation loop (§9.7, max 2 iterations)
- [ ] N-gram overlap check (anti-verbatim)

### Schemas
- [ ] `schemas/voice_profile.schema.json`
- [ ] `schemas/draft_package.schema.json`

### Output Package
- [ ] `draft_package.json` generation
- [ ] `preview.md` generation

### End-to-End Testing
- [ ] Full pipeline test: handle → corpus → profile → draft → humanized output
- [ ] Verify lint report completeness
- [ ] Verify self-eval loop triggers rewrite when needed
