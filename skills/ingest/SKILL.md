---
name: KOL Voice 收集
description: 使用 chrome-dev-mcp 瀏覽器自動化從 KOL 的 X 個人頁面收集約 500 篇貼文，正規化後儲存為語料庫產物。
---

# Skill A — 收集（透過 `chrome-dev-mcp`）

從目標 KOL 的 X (Twitter) 個人頁面收集貼文，使用瀏覽器自動化操作。輸出正規化的語料庫 JSONL 檔案 + metadata JSON。

## 前置條件

- `chrome-dev-mcp` MCP 伺服器必須已連線且可用。
- Chrome 瀏覽器需已登入 X (twitter.com)，以便能存取時間線。
- 使用者已提供有效的 X `handle`（不含 @ 前綴）。

---

## 步驟 1：導航至 KOL 個人頁面

使用 `chrome-dev-mcp` 導航至 KOL 的個人頁面：

```
URL: https://x.com/<handle>
```

等待頁面完全載入。確認頁面顯示的是 KOL 的時間線（檢查 DOM 中是否有推文/貼文元素）。

如果頁面顯示登入牆或錯誤，告知使用者：
_「無法存取 @{handle} 的頁面，請確認 Chrome 已登入 X 並且該帳號存在。」_

---

## 步驟 2：透過滾動收集貼文

重複以下迴圈，直到收集到**約 500 篇貼文**或沒有新貼文載入為止：

### 2.1 提取可見的貼文
對頁面上每個可見的推文/貼文元素，提取以下欄位：

| 欄位 | 如何提取 | 備註 |
|---|---|---|
| `text_raw` | 貼文的完整文字內容 | 保留換行、emoji、所有字元，完全照原樣 |
| `created_at` | 時間戳記元素（datetime 屬性或顯示文字） | 盡可能轉換為 ISO 8601 格式 |
| `like_count` | 愛心/按讚數 | 解析數字，預設 0 |
| `reply_count` | 回覆數 | 解析數字，預設 0 |
| `repost_count` | 轉推/轉發數 | 解析數字，預設 0 |
| `urls` | 貼文中的任何 URL | 提取原始 URL |
| `mentions` | 文字中的任何 @提及 | 提取時不含 @ 前綴 |
| `hashtags` | 文字中的任何 #標籤 | 提取時不含 # 前綴 |

### 2.2 去重
- 追蹤貼文 ID（如果沒有 ID，使用 `text_raw` 的 hash），避免重複收集同一篇貼文。
- 跳過其他帳號的轉推/轉發 — 只收集 KOL 本人的原創貼文。
- 跳過回覆推文（以 @提及開頭的），除非是 KOL 自己的討論串。

### 2.3 向下滾動
- 將頁面向下滾動以載入更多貼文。
- 每次滾動後**等待 1–2 秒**，讓新貼文載入。
- 檢查是否有新貼文出現。如果連續 3 次滾動都沒有新貼文出現，表示收集完成。

### 2.4 進度追蹤
- 每收集約 50 篇貼文，在內部記錄一下數量。
- 如果過程中斷，已收集的部分語料庫仍然可用。

---

## 步驟 3：正規化貼文

對每篇收集到的貼文，建立正規化紀錄：

### 3.1 從 `text_raw` 生成 `text_norm`
依序套用以下轉換：
1. 將所有 URL（http/https 連結）替換為 `<URL>`
2. 將所有 @提及 替換為 `<MENTION>`
3. 將所有 #標籤 替換為 `<HASHTAG:標籤名稱>`（保留標籤名稱，移除 #）
4. 將多個連續空格合併為一個空格
5. **保留換行** — 換行是重要的風格訊號

### 3.2 偵測語言
- 判斷 `text_norm` 的主要語言（en、zh、ja、ko 等）
- 使用簡單啟發式方法：如果大部分字元是 CJK → zh；否則 → en
- 存入 `lang` 欄位

### 3.3 過濾非內容貼文（選擇性）
- 移除 `text_norm` 在去除 token（`<URL>`、`<MENTION>`、`<HASHTAG:...>`）後字元數少於 15 的貼文
- 這類通常是純媒體貼文，沒有文字訊號

---

## 步驟 4：寫入輸出產物

將兩個檔案寫入 `artifacts/kol/<handle>/`：

### 4.1 `corpus.v1.jsonl`
每行一個 JSON 物件。每筆紀錄格式：

```json
{
  "id": "<post_id_or_hash>",
  "created_at": "2026-02-28T12:34:56Z",
  "text_raw": "原始貼文文字完全照原樣...",
  "text_norm": "貼文文字 <URL> <MENTION> <HASHTAG:buildinpublic> ...",
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
    "fetched_at": "<當前_ISO_時間戳>"
  }
}
```

### 4.2 `corpus_meta.json`
```json
{
  "handle": "<handle>",
  "count": <貼文數量>,
  "via": "chrome-dev-mcp",
  "built_at": "<當前_ISO_時間戳>",
  "pagination": {
    "last_seen_post_id": "<收集到的最舊貼文 ID>",
    "scroll_position": "<大約滾動深度>"
  }
}
```

---

## 步驟 5：回報結果給使用者

寫入產物後，向使用者報告摘要：

```
✅ 已收集 @{handle} 的推文語料庫

- 總共收集：{count} 篇推文
- 語言分佈：{en_pct}% English, {zh_pct}% 中文, {other_pct}% 其他
- 時間範圍：{oldest_date} ~ {newest_date}
- 儲存位置：artifacts/kol/{handle}/corpus.v1.jsonl
```

---

## 增量更新模式

如果此 handle 的 `corpus.v1.jsonl` 和 `corpus_meta.json` 已存在：

1. 讀取 `corpus_meta.json` 取得 `last_seen_post_id` 和 `built_at`
2. 告知使用者：_「已有 {count} 篇推文（上次收集：{built_at}）。要增量更新還是全部重新收集？」_
3. 如果選擇增量更新：只收集比 `last_seen_post_id` 更新的貼文，然後**附加**到現有語料庫並更新 metadata
4. 如果選擇全部重新收集：覆寫兩個檔案

---

## 錯誤處理

| 情況 | 處理方式 |
|---|---|
| chrome-dev-mcp 未連線 | 告知使用者：「請確認 chrome-dev-mcp MCP server 已連線」 |
| 個人頁面不存在（404） | 告知使用者：「找不到 @{handle}，請確認 handle 正確」 |
| 登入牆 / 受限 | 告知使用者：「請確認 Chrome 已登入 X」 |
| 被限速（載入緩慢） | 將滾動間隔增加到 3-5 秒，重試 |
| 收集不到 50 篇 | 警告使用者：「只收集到 {count} 篇推文，風格分析可能不夠準確」 |
