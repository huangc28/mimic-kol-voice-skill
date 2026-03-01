#!/usr/bin/env python3
"""
KOL Voice Ingest — Scrape X (Twitter) posts from a KOL's profile.

Uses Playwright to automate a real Chromium browser, scroll through
the KOL's timeline, and collect posts into a normalized JSONL corpus.

Usage:
    # Activate venv first
    source .venv/bin/activate

    # Basic usage (collects ~200 posts by default)
    python scripts/ingest.py --handle marclou

    # Collect more posts
    python scripts/ingest.py --handle marclou --limit 500

    # Custom output directory
    python scripts/ingest.py --handle marclou --out artifacts/kol/marclou
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone


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
    # Replace URLs with <URL>
    text = re.sub(r"https?://\S+", "<URL>", text)
    # Replace @mentions with <MENTION>
    text = re.sub(r"@(\w+)", "<MENTION>", text)
    # Replace #hashtags with <HASHTAG:tag>
    text = re.sub(r"#(\w+)", lambda m: f"<HASHTAG:{m.group(1)}>", text)
    # Collapse multiple spaces (but preserve newlines)
    text = re.sub(r"[^\S\n]+", " ", text)
    return text.strip()


def detect_language(text: str) -> str:
    """Simple heuristic language detection."""
    # Remove tokens
    clean = re.sub(r"<URL>|<MENTION>|<HASHTAG:\w+>", "", text).strip()
    if not clean:
        return "unknown"
    # Count CJK characters
    cjk_count = sum(1 for c in clean if "\u4e00" <= c <= "\u9fff"
                     or "\u3400" <= c <= "\u4dbf"
                     or "\uf900" <= c <= "\ufaff")
    # If > 30% CJK, classify as zh
    if len(clean) > 0 and cjk_count / len(clean) > 0.3:
        return "zh"
    return "en"


def extract_entities(raw: str) -> dict:
    """Extract hashtags, mentions, and URLs from raw text."""
    urls = re.findall(r"https?://\S+", raw)
    mentions = re.findall(r"@(\w+)", raw)
    hashtags = re.findall(r"#(\w+)", raw)
    return {
        "hashtags": hashtags,
        "mentions": mentions,
        "urls": urls,
    }


def make_post_id(text: str, timestamp: str) -> str:
    """Generate a stable ID from text + timestamp."""
    content = f"{text}:{timestamp}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


JS_EXTRACT_TWEETS = """
() => {
    const tweets = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
    return tweets.map(tweet => {
        // Get tweet text
        const textEl = tweet.querySelector('[data-testid="tweetText"]');
        const text = textEl ? textEl.innerText : "";

        // Get timestamp
        const timeEl = tweet.querySelector('time');
        const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;

        // Get tweet URL (contains the tweet ID)
        const linkEl = timeEl ? timeEl.closest('a') : null;
        const tweetUrl = linkEl ? linkEl.getAttribute('href') : null;

        // Extract tweet ID from URL like /marclou/status/1234567890
        let tweetId = null;
        if (tweetUrl) {
            const match = tweetUrl.match(/\\/status\\/(\\d+)/);
            if (match) tweetId = match[1];
        }

        // Get metrics
        const replyBtn = tweet.querySelector('[data-testid="reply"]');
        const retweetBtn = tweet.querySelector('[data-testid="retweet"]');
        const likeBtn = tweet.querySelector('[data-testid="like"]');

        // Metric text is in an aria-label or inner span
        const getMetric = (btn) => {
            if (!btn) return "0";
            const label = btn.getAttribute('aria-label') || "";
            const match = label.match(/([\\d,.]+[KMB]?)/i);
            if (match) return match[1];
            // Fallback: try inner text
            const span = btn.querySelector('span span');
            return span ? span.innerText : "0";
        };

        // Check if this is a retweet (has "Retweeted" or similar indicator)
        const socialContext = tweet.querySelector('[data-testid="socialContext"]');
        const isRetweet = socialContext ?
            socialContext.innerText.toLowerCase().includes('repost') ||
            socialContext.innerText.toLowerCase().includes('retweeted') : false;

        // Check if reply (starts with "Replying to")
        const replyIndicator = tweet.querySelector('[data-testid="Tweet-User-Avatar"]');

        return {
            text: text,
            timestamp: timestamp,
            tweet_id: tweetId,
            tweet_url: tweetUrl,
            reply_count: getMetric(replyBtn),
            repost_count: getMetric(retweetBtn),
            like_count: getMetric(likeBtn),
            is_retweet: isRetweet
        };
    });
}
"""


def scrape_profile(handle: str, limit: int = 200, delay: float = 2.0, headless: bool = False):
    """Scrape posts from a KOL's X profile using Playwright."""
    from playwright.sync_api import sync_playwright

    print(f"🚀 Starting ingest for @{handle} (target: {limit} posts)")

    collected = {}  # tweet_id -> post dict
    no_new_count = 0
    max_no_new = 5  # stop after 5 scrolls with no new tweets

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Navigate to profile
        url = f"https://x.com/{handle}"
        print(f"📄 Navigating to {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)  # Wait for dynamic content to load

        # Wait for tweets to appear (retry a few times)
        tweet_found = False
        for attempt in range(5):
            if page.query_selector('article[data-testid="tweet"]'):
                tweet_found = True
                break
            print(f"  ⏳ Waiting for tweets to load (attempt {attempt + 1}/5)...")
            page.wait_for_timeout(3000)

        if not tweet_found:
            print("❌ Could not find any tweets. The profile may not exist or Chrome may need to be logged in.")
            browser.close()
            return []

        scroll_count = 0
        while len(collected) < limit and no_new_count < max_no_new:
            # Extract tweets from current view
            raw_tweets = page.evaluate(JS_EXTRACT_TWEETS)
            prev_count = len(collected)

            for tweet in raw_tweets:
                text = tweet.get("text", "").strip()
                if not text:
                    continue

                # Skip retweets
                if tweet.get("is_retweet", False):
                    continue

                # Generate ID
                tweet_id = tweet.get("tweet_id") or make_post_id(text, tweet.get("timestamp", ""))

                if tweet_id in collected:
                    continue

                # Build normalized record
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
                        "via": "playwright",
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
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(int(delay * 1000))

            # Handle potential login walls or popups
            close_btn = page.query_selector('[data-testid="xMigrationBottomBar"] button')
            if close_btn:
                close_btn.click()
                page.wait_for_timeout(500)

        browser.close()

    posts = list(collected.values())
    print(f"\n✅ Collection complete: {len(posts)} posts from @{handle}")
    return posts


def write_corpus(posts: list, handle: str, output_dir: str):
    """Write corpus JSONL and metadata files."""
    os.makedirs(output_dir, exist_ok=True)

    # Write corpus JSONL
    corpus_path = os.path.join(output_dir, "corpus.v1.jsonl")
    with open(corpus_path, "w", encoding="utf-8") as f:
        for post in posts:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")

    # Compute language mix
    lang_counts = {}
    for post in posts:
        lang = post.get("lang", "unknown")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    total = len(posts) or 1
    language_mix = {lang: round(count / total, 2) for lang, count in lang_counts.items()}

    # Find date range
    timestamps = [p["created_at"] for p in posts if p.get("created_at")]
    oldest = min(timestamps) if timestamps else ""
    newest = max(timestamps) if timestamps else ""

    # Write metadata
    meta = {
        "handle": handle,
        "count": len(posts),
        "via": "playwright",
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


def main():
    parser = argparse.ArgumentParser(
        description="KOL Voice Ingest — Scrape X posts from a KOL's profile"
    )
    parser.add_argument("--handle", required=True, help="X handle (without @)")
    parser.add_argument("--limit", type=int, default=200, help="Max posts to collect (default: 200)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between scrolls in seconds (default: 2.0)")
    parser.add_argument("--out", default=None, help="Output directory (default: artifacts/kol/<handle>)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    args = parser.parse_args()

    output_dir = args.out or os.path.join("artifacts", "kol", args.handle)

    # Scrape
    posts = scrape_profile(
        handle=args.handle,
        limit=args.limit,
        delay=args.delay,
        headless=args.headless,
    )

    if not posts:
        print("❌ No posts collected. Exiting.")
        sys.exit(1)

    # Write output
    write_corpus(posts, args.handle, output_dir)


if __name__ == "__main__":
    main()
