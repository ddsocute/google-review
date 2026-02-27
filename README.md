# Google Map Review - 信義區預建資料庫

本專案支援把「台北市信義區」店家先批次建檔（店家清單 + 評論 AI 分析快取），
讓使用者日後查詢時大多數情況能直接命中 `analysis_cache.db`，大幅縮短等待時間。

## Vercel 部署（持久化快取 / 店家清單）

Vercel 上的本地檔案系統是 **暫存**（SQLite 會落在 `/tmp`，不會持久化），
因此若你希望「第二次查詢直接命中快取」與「保留以前分析過的店家清單」，
請在 Vercel 建立 Postgres（例如 Neon / Vercel Postgres）並設定環境變數：

- `POSTGRES_URL`

程式會在偵測到 `POSTGRES_URL` 時，將以下表改存 Postgres：
- `analysis_cache`
- `places`
- `place_catalog`

### 把本機 SQLite 舊資料搬到 Postgres（一次性）

1) 將 `POSTGRES_URL` 放進本機 `.env`（可參考 `env.example`）
2) 執行：

```bash
venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py
```

## 重要說明（關於「抓到所有信義區餐廳」）

Google / Apify 的搜尋結果本質上是「排序後的前 N 筆」，單一關鍵字一定會漏。
此專案的做法是用 **大量關鍵字（類別/地標/捷運站/路名）疊加**，再用：
- 地址包含 `信義區`（優先）
- 或座標落在信義區的寬鬆 bounding box（備援）

來做過濾與去重，以「盡可能接近全量」為目標。

## 一次性建庫（發現店家 + 批次分析）

在專案根目錄執行：

```bash
venv\Scripts\python.exe scripts\build_xinyi_db.py
```

常用參數：
- `--discover-only`：只撈店家清單、不跑分析
- `--analyze-only`：只跑分析（搭配已存在的 catalog）
- `--limit-per-query 200`：每個搜尋字串最多抓幾間（越大越慢、越接近全量）
- `--max-places 200`：只分析前 N 間（測試用）
- `--force-refresh`：忽略快取直接重跑分析

## 每週更新（只會重跑過期快取）

`analysis_cache` 預設 TTL 是 7 天（見 `services/cache_store.py`），因此每週跑一次即可：

```bash
venv\Scripts\python.exe scripts\update_xinyi_weekly.py
```

## 產出資料

- `data/analysis_cache.db`
  - `analysis_cache`：分析結果快取（7 天 TTL）
  - `places`：已分析過的店家清單
  - `place_catalog`：信義區候選店家目錄（可重跑、可續跑）

