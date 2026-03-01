---
name: KOL Voice Ingest
description: Collect ~500 posts from a KOL's X profile using chrome-dev-mcp browser automation, normalize and store as corpus artifacts.
---

# Skill A — Ingest (via `chrome-dev-mcp`)

Collect posts from a target KOL's X (Twitter) profile page using browser automation. Output a normalized corpus as JSONL + metadata JSON.

## Prerequisites

- `chrome-dev-mcp` MCP server must be connected and available.
- The Chrome browser should be logged into X (twitter.com) so that the timeline is accessible.
- The user has provided a valid X `handle` (without the @ prefix).

---

## Step 1: Navigate to KOL Profile

Use `chrome-dev-mcp` to navigate to the KOL's profile:

```
URL: https://x.com/<handle>
```

Wait for the page to fully load. Verify that the profile page is showing the KOL's timeline (look for tweet/post elements in the DOM).

If the page shows a login wall or error, inform the user:
_"無法存取 @{handle} 的頁面，請確認 Chrome 已登入 X 並且該帳號存在。"_

---

## Step 2: Collect Posts by Scrolling

Repeat the following loop until you have collected **~500 posts** or no new posts are loading:

### 2.1 Extract visible posts
For each tweet/post element visible on the page, extract:

| Field | How to extract | Notes |
|---|---|---|
| `text_raw` | The full post text content | Preserve line breaks, emojis, all characters exactly as shown |
| `created_at` | The timestamp element (datetime attribute or visible text) | Convert to ISO 8601 format if possible |
| `like_count` | The like/heart count | Parse number, default 0 |
| `reply_count` | The reply count | Parse number, default 0 |
| `repost_count` | The repost/retweet count | Parse number, default 0 |
| `urls` | Any URLs in the post text | Extract raw URLs |
| `mentions` | Any @mentions in the text | Extract without @ prefix |
| `hashtags` | Any #hashtags in the text | Extract without # prefix |

### 2.2 Deduplication
- Track post IDs (or use `text_raw` hash if no ID is available) to avoid collecting the same post twice.
- Skip retweets/reposts from other accounts — only collect the KOL's own posts.
- Skip posts that are replies (start with @mention) unless they are part of the KOL's own thread.

### 2.3 Scroll down
- Scroll the page down to load more posts.
- **Wait 1–2 seconds** after each scroll to allow new posts to load.
- Check if new posts appeared. If no new posts appear after 3 consecutive scroll attempts, the collection is complete.

### 2.4 Progress tracking
- After every ~50 posts collected, note the count internally.
- If the process is interrupted, the partial corpus should still be usable.

---

## Step 3: Normalize Posts

For each collected post, create a normalized record:

### 3.1 Generate `text_norm` from `text_raw`
Apply these transformations (in order):
1. Replace all URLs (http/https links) with `<URL>`
2. Replace all @mentions with `<MENTION>`
3. Replace all #hashtags with `<HASHTAG:tagname>` (keep the tag name, remove #)
4. Collapse multiple consecutive spaces into one space
5. **Keep line breaks** — they are important style signals

### 3.2 Detect language
- Determine the primary language of `text_norm` (en, zh, ja, ko, etc.)
- Use simple heuristics: if majority of characters are CJK → zh; otherwise → en
- Store as `lang` field

### 3.3 Filter non-content posts (optional)
- Drop posts where `text_norm` after removing tokens (`<URL>`, `<MENTION>`, `<HASHTAG:...>`) has fewer than 15 characters
- These are typically media-only posts with no text signal

---

## Step 4: Write Output Artifacts

Write two files to `artifacts/kol/<handle>/`:

### 4.1 `corpus.v1.jsonl`
One JSON object per line. Each record:

```json
{
  "id": "<post_id_or_hash>",
  "created_at": "2026-02-28T12:34:56Z",
  "text_raw": "original post text exactly as shown...",
  "text_norm": "post text with <URL> <MENTION> <HASHTAG:buildinpublic> ...",
  "lang": "en",
  "entities": {
    "hashtags": ["buildinpublic"],
    "mentions": ["someone"],
    "urls": ["https://t.co/abc123"]
  },
  "public_metrics": {
    "like_count": 10,
    "reply_count": 2,
    "repost_count": 1,
    "quote_count": 0
  },
  "source": {
    "platform": "x",
    "handle": "<handle>",
    "via": "chrome-dev-mcp",
    "fetched_at": "<current_ISO_timestamp>"
  }
}
```

### 4.2 `corpus_meta.json`
```json
{
  "handle": "<handle>",
  "count": <number_of_posts>,
  "via": "chrome-dev-mcp",
  "built_at": "<current_ISO_timestamp>",
  "pagination": {
    "last_seen_post_id": "<id_of_oldest_post_collected>",
    "scroll_position": "<approximate_scroll_depth>"
  }
}
```

---

## Step 5: Report to User

After writing the artifacts, report a summary:

```
✅ 已收集 @{handle} 的推文語料庫

- 總共收集：{count} 篇推文
- 語言分佈：{en_pct}% English, {zh_pct}% 中文, {other_pct}% 其他
- 時間範圍：{oldest_date} ~ {newest_date}
- 儲存位置：artifacts/kol/{handle}/corpus.v1.jsonl
```

---

## Incremental Update Mode

If `corpus.v1.jsonl` and `corpus_meta.json` already exist for this handle:

1. Read `corpus_meta.json` to get `last_seen_post_id` and `built_at`
2. Inform user: _"已有 {count} 篇推文（上次收集：{built_at}）。要增量更新還是全部重新收集？"_
3. If incremental: only collect posts newer than `last_seen_post_id`, then **append** to existing corpus and update metadata
4. If full re-collect: overwrite both files

---

## Error Handling

| Situation | Action |
|---|---|
| chrome-dev-mcp not connected | Tell user: "請確認 chrome-dev-mcp MCP server 已連線" |
| Profile page not found (404) | Tell user: "找不到 @{handle}，請確認 handle 正確" |
| Login wall / restricted | Tell user: "請確認 Chrome 已登入 X" |
| Rate limited (slow loading) | Increase delay between scrolls to 3-5 seconds, retry |
| Collected < 50 posts | Warn user: "只收集到 {count} 篇推文，風格分析可能不夠準確" |
