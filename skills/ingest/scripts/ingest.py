#!/usr/bin/env python3
"""
KOL Voice Ingest — Scrape X (Twitter) posts from a KOL's profile.

Connects to an existing Chrome browser via Chrome DevTools Protocol (CDP)
on port 9222, navigates to the KOL's profile, scrolls through the timeline,
and collects posts into a normalized JSONL corpus.

Prerequisites:
    1. Launch Chrome with remote debugging:
       /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
         --remote-debugging-port=9222

    2. Activate venv:
       source .venv/bin/activate

Usage:
    # Basic usage (collects ~200 posts by default)
    python skills/ingest/scripts/ingest.py --handle marclou

    # Collect more posts
    python skills/ingest/scripts/ingest.py --handle marclou --limit 500

    # Custom CDP port
    python skills/ingest/scripts/ingest.py --handle marclou --port 9222
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

import websocket


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def parse_metric(text: str) -> int:
    """Parse metric text like '1.2K', '15', '' into integer."""
    if not text or not text.strip():
        return 0
    text = text.strip().replace(",", "")
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if text.upper().endswith(suffix):
            try:
                return int(float(text[:-1]) * mult)
            except ValueError:
                return 0
    try:
        return int(text)
    except ValueError:
        return 0


def normalize_text(raw: str) -> str:
    """Apply normalization rules: replace URLs, mentions, hashtags."""
    text = raw
    text = re.sub(r"https?://\S+", "<URL>", text)
    text = re.sub(r"@(\w+)", "<MENTION>", text)
    text = re.sub(r"#(\w+)", lambda m: f"<HASHTAG:{m.group(1)}>", text)
    text = re.sub(r"[^\S\n]+", " ", text)
    return text.strip()


def detect_language(text: str) -> str:
    """Simple heuristic language detection."""
    clean = re.sub(r"<URL>|<MENTION>|<HASHTAG:\w+>", "", text).strip()
    if not clean:
        return "unknown"
    cjk_count = sum(1 for c in clean if "\u4e00" <= c <= "\u9fff"
                     or "\u3400" <= c <= "\u4dbf"
                     or "\uf900" <= c <= "\ufaff")
    if len(clean) > 0 and cjk_count / len(clean) > 0.3:
        return "zh"
    return "en"


def extract_entities(raw: str) -> dict:
    """Extract hashtags, mentions, and URLs from raw text."""
    return {
        "hashtags": re.findall(r"#(\w+)", raw),
        "mentions": re.findall(r"@(\w+)", raw),
        "urls": re.findall(r"https?://\S+", raw),
    }


def make_post_id(text: str, timestamp: str) -> str:
    """Generate a stable ID from text + timestamp."""
    content = f"{text}:{timestamp}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# CDP client
# ---------------------------------------------------------------------------

class CDPClient:
    """Minimal Chrome DevTools Protocol client over WebSocket."""

    def __init__(self, port: int = 9222):
        self.port = port
        self._msg_id = 0
        self.ws = None

    def connect(self):
        """Connect to the first available Chrome tab."""
        url = f"http://localhost:{self.port}/json"
        try:
            resp = urllib.request.urlopen(url, timeout=5)
        except Exception as e:
            raise ConnectionError(
                f"Cannot connect to Chrome on port {self.port}. "
                f"Launch Chrome with: --remote-debugging-port={self.port}"
            ) from e

        targets = json.loads(resp.read())
        # Find a page target (not devtools, not background)
        page_target = None
        for t in targets:
            if t.get("type") == "page":
                page_target = t
                break

        if not page_target:
            raise ConnectionError("No page tab found in Chrome. Open at least one tab.")

        ws_url = page_target["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=30)
        print(f"  🔗 Connected to Chrome tab: {page_target.get('title', 'unknown')}")

    def send(self, method: str, params: dict = None) -> dict:
        """Send a CDP command and wait for the result."""
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))

        # Wait for matching response
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == self._msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                return data.get("result", {})

    def navigate(self, url: str):
        """Navigate to a URL and wait for load."""
        self.send("Page.navigate", {"url": url})
        # Wait for Page.loadEventFired
        deadline = time.time() + 30
        while time.time() < deadline:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("method") == "Page.loadEventFired":
                break

    def evaluate(self, expression: str):
        """Evaluate JavaScript and return the result."""
        result = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        })
        val = result.get("result", {})
        if val.get("type") == "undefined":
            return None
        return val.get("value")

    def close(self):
        if self.ws:
            self.ws.close()


# ---------------------------------------------------------------------------
# JS extraction code (runs in browser context)
# ---------------------------------------------------------------------------

JS_EXTRACT_TWEETS = """
(() => {
    const tweets = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
    return tweets.map(tweet => {
        const textEl = tweet.querySelector('[data-testid="tweetText"]');
        const text = textEl ? textEl.innerText : "";

        const timeEl = tweet.querySelector('time');
        const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;

        const linkEl = timeEl ? timeEl.closest('a') : null;
        const tweetUrl = linkEl ? linkEl.getAttribute('href') : null;

        let tweetId = null;
        if (tweetUrl) {
            const match = tweetUrl.match(/\\/status\\/(\\d+)/);
            if (match) tweetId = match[1];
        }

        const replyBtn = tweet.querySelector('[data-testid="reply"]');
        const retweetBtn = tweet.querySelector('[data-testid="retweet"]');
        const likeBtn = tweet.querySelector('[data-testid="like"]');

        const getMetric = (btn) => {
            if (!btn) return "0";
            const label = btn.getAttribute('aria-label') || "";
            const match = label.match(/([\\d,.]+[KMB]?)/i);
            if (match) return match[1];
            const span = btn.querySelector('span span');
            return span ? span.innerText : "0";
        };

        const socialContext = tweet.querySelector('[data-testid="socialContext"]');
        const isRetweet = socialContext ?
            socialContext.innerText.toLowerCase().includes('repost') ||
            socialContext.innerText.toLowerCase().includes('retweeted') : false;

        return {
            text, timestamp, tweet_id: tweetId, tweet_url: tweetUrl,
            reply_count: getMetric(replyBtn),
            repost_count: getMetric(retweetBtn),
            like_count: getMetric(likeBtn),
            is_retweet: isRetweet
        };
    });
})()
"""

JS_SCROLL_DOWN = "window.scrollBy(0, 1200)"

JS_DISMISS_POPUPS = """
(() => {
    // Dismiss "Don't miss what's happening" bottom bar
    const bar = document.querySelector('[data-testid="xMigrationBottomBar"]');
    if (bar) { const btn = bar.querySelector('button'); if (btn) btn.click(); }

    // Dismiss any "Log in" dialog close buttons
    const closeButtons = document.querySelectorAll('[data-testid="app-bar-close"], [aria-label="Close"]');
    closeButtons.forEach(b => b.click());
})()
"""


# ---------------------------------------------------------------------------
# Main scraping logic
# ---------------------------------------------------------------------------

def scrape_profile(handle: str, limit: int = 200, delay: float = 2.0, port: int = 9222):
    """Scrape posts from a KOL's X profile via CDP."""
    print(f"🚀 Starting ingest for @{handle} (target: {limit} posts)")

    cdp = CDPClient(port=port)
    try:
        cdp.connect()
    except ConnectionError as e:
        print(f"❌ {e}")
        return []

    # Enable Page events
    cdp.send("Page.enable")

    # Navigate to profile
    url = f"https://x.com/{handle}"
    print(f"📄 Navigating to {url}")
    cdp.navigate(url)
    time.sleep(5)  # Wait for dynamic content

    # Wait for tweets to appear
    tweet_found = False
    for attempt in range(8):
        count = cdp.evaluate(
            'document.querySelectorAll(\'article[data-testid="tweet"]\').length'
        )
        if count and count > 0:
            tweet_found = True
            break
        print(f"  ⏳ Waiting for tweets to load (attempt {attempt + 1}/8)...")
        time.sleep(3)

    if not tweet_found:
        print("❌ Could not find any tweets. The profile may not exist or you may need to log in.")
        cdp.close()
        return []

    # Dismiss any popups
    cdp.evaluate(JS_DISMISS_POPUPS)

    collected = {}
    no_new_count = 0
    max_no_new = 5
    scroll_count = 0

    while len(collected) < limit and no_new_count < max_no_new:
        # Extract tweets from current view
        raw_tweets = cdp.evaluate(JS_EXTRACT_TWEETS)
        if not raw_tweets:
            raw_tweets = []

        prev_count = len(collected)

        for tweet in raw_tweets:
            text = tweet.get("text", "").strip()
            if not text:
                continue

            if tweet.get("is_retweet", False):
                continue

            tweet_id = tweet.get("tweet_id") or make_post_id(text, tweet.get("timestamp", ""))
            if tweet_id in collected:
                continue

            text_norm = normalize_text(text)
            entities = extract_entities(text)
            lang = detect_language(text_norm)

            # Filter non-content posts
            clean_text = re.sub(r"<URL>|<MENTION>|<HASHTAG:\w+>", "", text_norm).strip()
            if len(clean_text) < 15:
                continue

            post = {
                "id": tweet_id,
                "created_at": tweet.get("timestamp", ""),
                "text_raw": text,
                "text_norm": text_norm,
                "lang": lang,
                "entities": entities,
                "public_metrics": {
                    "like_count": parse_metric(tweet.get("like_count", "0")),
                    "reply_count": parse_metric(tweet.get("reply_count", "0")),
                    "repost_count": parse_metric(tweet.get("repost_count", "0")),
                    "quote_count": 0,
                },
                "source": {
                    "platform": "x",
                    "handle": handle,
                    "via": "cdp",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            collected[tweet_id] = post

        new_count = len(collected) - prev_count
        scroll_count += 1

        if new_count == 0:
            no_new_count += 1
        else:
            no_new_count = 0

        print(f"  📜 Scroll #{scroll_count}: +{new_count} new posts (total: {len(collected)}/{limit})")

        if len(collected) >= limit:
            break

        # Scroll down
        cdp.evaluate(JS_SCROLL_DOWN)
        time.sleep(delay)

        # Dismiss popups that may appear while scrolling
        cdp.evaluate(JS_DISMISS_POPUPS)

    cdp.close()

    posts = list(collected.values())
    print(f"\n✅ Collection complete: {len(posts)} posts from @{handle}")
    return posts


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

def write_corpus(posts: list, handle: str, output_dir: str):
    """Write corpus JSONL and metadata files."""
    os.makedirs(output_dir, exist_ok=True)

    corpus_path = os.path.join(output_dir, "corpus.v1.jsonl")
    with open(corpus_path, "w", encoding="utf-8") as f:
        for post in posts:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")

    lang_counts = {}
    for post in posts:
        lang = post.get("lang", "unknown")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    total = len(posts) or 1
    language_mix = {lang: round(count / total, 2) for lang, count in lang_counts.items()}

    timestamps = [p["created_at"] for p in posts if p.get("created_at")]
    oldest = min(timestamps) if timestamps else ""
    newest = max(timestamps) if timestamps else ""

    meta = {
        "handle": handle,
        "count": len(posts),
        "via": "cdp",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "language_mix": language_mix,
        "date_range": {"oldest": oldest, "newest": newest},
        "pagination": {
            "last_seen_post_id": posts[-1]["id"] if posts else "",
        },
    }

    meta_path = os.path.join(output_dir, "corpus_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n📁 Artifacts written to {output_dir}/")
    print(f"   - corpus.v1.jsonl ({len(posts)} records)")
    print(f"   - corpus_meta.json")
    print(f"   - Language mix: {language_mix}")
    print(f"   - Date range: {oldest[:10] if oldest else '?'} ~ {newest[:10] if newest else '?'}")

    return corpus_path, meta_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="KOL Voice Ingest — Scrape X posts via Chrome DevTools Protocol"
    )
    parser.add_argument("--handle", required=True, help="X handle (without @)")
    parser.add_argument("--limit", type=int, default=200, help="Max posts to collect (default: 200)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between scrolls in seconds (default: 2.0)")
    parser.add_argument("--port", type=int, default=9222, help="Chrome remote debugging port (default: 9222)")
    parser.add_argument("--out", default=None, help="Output directory (default: artifacts/kol/<handle>)")

    args = parser.parse_args()
    output_dir = args.out or os.path.join("artifacts", "kol", args.handle)

    posts = scrape_profile(
        handle=args.handle,
        limit=args.limit,
        delay=args.delay,
        port=args.port,
    )

    if not posts:
        print("❌ No posts collected. Exiting.")
        sys.exit(1)

    write_corpus(posts, args.handle, output_dir)


if __name__ == "__main__":
    main()
