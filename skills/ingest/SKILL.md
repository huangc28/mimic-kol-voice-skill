---
name: KOL Voice Ingest
description: Collect ~500 posts from a KOL's X profile via Chrome DevTools Protocol (CDP), normalize and store as corpus artifacts.
---

# Skill A — Ingest (via CDP)

Collect posts from a target KOL's X (Twitter) profile page by connecting to your existing Chrome browser via Chrome DevTools Protocol (CDP). Output a normalized corpus as JSONL + metadata JSON.

## Prerequisites

- Python venv with `websocket-client` installed (`.venv/`)
- Chrome launched with remote debugging enabled:
  ```bash
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
  ```
- Chrome logged into X (twitter.com) so the timeline is accessible
- The user has provided a valid X `handle` (without the @ prefix)

---

## Quick Start

Run the ingest script from the project root:

```bash
source .venv/bin/activate
python skills/ingest/scripts/ingest.py --handle <handle> --limit 500
```

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `--handle` | (required) | KOL's X handle (without @) |
| `--limit` | 200 | Maximum number of posts to collect |
| `--delay` | 2.0 | Delay between scrolls in seconds |
| `--port` | 9222 | Chrome remote debugging port |
| `--out` | `artifacts/kol/<handle>` | Output directory for corpus files |

---

## What the Script Does

### Step 1: Connect & navigate
- Connects to your existing Chrome via WebSocket on port 9222
- Navigates to `https://x.com/<handle>` in the current tab
- Waits for tweet elements to appear in the DOM
- Reports error if Chrome not reachable, profile not found, or login needed

### Step 2: Scroll and collect posts
- Extracts tweets using `data-testid` DOM selectors:
  - `article[data-testid="tweet"]` — tweet container
  - `[data-testid="tweetText"]` — post text
  - `time[datetime]` — timestamp
  - `[data-testid="reply/retweet/like"]` — engagement metrics
- Scrolls down with configurable delay between scrolls
- Deduplicates by tweet ID
- Skips retweets from other accounts
- Stops after collecting `--limit` posts or 5 consecutive scrolls with no new content

### Step 3: Normalize
- Generates `text_norm` from `text_raw`:
  - URLs → `<URL>`
  - @mentions → `<MENTION>`
  - #hashtags → `<HASHTAG:tagname>`
  - Collapses spaces, preserves line breaks
- Detects language (CJK heuristic → zh, otherwise → en)
- Filters non-content posts (< 15 chars after token removal)

### Step 4: Write output
- `corpus.v1.jsonl` — one JSON record per line
- `corpus_meta.json` — handle, count, language mix, date range

---

## Output Format

### `corpus.v1.jsonl` (each line)
```json
{
  "id": "1611708982841819137",
  "created_at": "2023-01-07T12:59:00.000Z",
  "text_raw": "7 years as an entrepreneur and 1 takeaway:\n\nShip more",
  "text_norm": "7 years as an entrepreneur and 1 takeaway:\n\nShip more",
  "lang": "en",
  "entities": {
    "hashtags": [],
    "mentions": [],
    "urls": []
  },
  "public_metrics": {
    "like_count": 11262,
    "reply_count": 521,
    "repost_count": 803,
    "quote_count": 0
  },
  "source": {
    "platform": "x",
    "handle": "marclou",
    "via": "playwright",
    "fetched_at": "2026-03-01T17:45:31.571584+00:00"
  }
}
```

### `corpus_meta.json`
```json
{
  "handle": "marclou",
  "count": 200,
  "via": "playwright",
  "built_at": "2026-03-01T17:45:54.845847+00:00",
  "language_mix": {"en": 1.0},
  "date_range": {
    "oldest": "2023-01-07T12:59:00.000Z",
    "newest": "2025-10-30T19:53:54.000Z"
  },
  "pagination": {
    "last_seen_post_id": "1845445649409638502"
  }
}
```

---

## Incremental Update Mode

If `corpus.v1.jsonl` and `corpus_meta.json` already exist for this handle:

1. Read `corpus_meta.json` to get `last_seen_post_id` and `built_at`
2. Inform the user that an existing corpus was found, and ask whether to do an incremental update or full re-collect
3. If incremental: only collect posts newer than `last_seen_post_id`, then **append** to existing corpus and update metadata
4. If full re-collect: overwrite both files

---

## Error Handling

| Situation | Action |
|---|---|
| Chrome not running on port 9222 | Launch Chrome with `--remote-debugging-port=9222` |
| `websocket-client` not installed | Run: `source .venv/bin/activate && pip install websocket-client` |
| Profile page not found (404) | Tell user the handle was not found and ask to verify |
| Login wall / restricted | Log into X in Chrome before running the script |
| Rate limited (slow loading) | Increase `--delay` to 3-5 seconds |
| Collected < 50 posts | Warn user that style analysis may be less accurate |
