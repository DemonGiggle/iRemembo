# iRemembo

Low-cost remembered-photo workflow.

## Design goals
- Only process images explicitly marked by the user as "記住"
- Keep repo safe to push to a remote
- Never store secrets/tokens/private runtime data in the repo
- Keep searchable metadata local; store retained image copies separately

## Repo vs local data
### In repo
- source code
- docs
- config templates
- helper scripts (including Dropbox helper code, but not Dropbox secrets)

### Local-only (outside repo)
Suggested path: a local-only folder outside the repo, for example `~/iRemembo-local/`
- real config
- SQLite DB
- thumbnails / derived images
- logs / runtime state
- tokens / secrets

## Quick start
1. Copy `config/example.local.json` to a local-only path, such as:
   `~/iRemembo-local/config.json`
2. Adjust paths/secrets locally
3. Export local-only env vars and run:
   ```bash
   export IREMEMBO_CONFIG=~/iRemembo-local/config.json
   export DROPBOX_CONFIG=~/secrets/dropbox.json
   python3 src/photo_memory.py init
   ```

## Current MVP commands
```bash
python3 src/photo_memory.py init
python3 src/photo_memory.py remember /path/to/image.jpg --auto-analyze --auto-embed
python3 src/photo_memory.py remember-chat /path/to/image.jpg --analysis-json '{"summary":"示例","tags":["標籤1"],"entities":{"objects":["照片"]},"ocr_text":""}' --auto-embed
python3 src/photo_memory.py remember /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2" --auto-embed
python3 src/photo_memory.py add /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2"
python3 src/photo_memory.py annotate 1 --summary "更新後摘要" --tags "標籤1,標籤2,標籤3"
python3 src/photo_memory.py embed 1
python3 src/photo_memory.py inspect /path/to/image.jpg
python3 src/photo_memory.py find 關鍵字
python3 src/photo_memory.py upload 1
python3 src/photo_memory.py fetch 1
```

## Current useful flow
1. 互動式主路徑：聊天裡先看圖，再把結果用 `remember-chat` 寫進 iRemembo
2. `remember-chat` 吃固定 JSON：`summary / tags / entities / ocr_text`
3. CLI 裡的 `remember --auto-analyze` 只會跑 `analysis_command`，不會用 OpenAI vision
4. 若要同時跑向量：`remember-chat ... --auto-embed` 或 `remember ... --auto-embed`
5. 預設會用 SHA-256 去重，遇到同圖直接回傳既有紀錄；若真的要重建可加 `--dedup allow-new`
6. 若要拆步驟：`add` 建立本機索引紀錄
7. `annotate` 寫回 OCR / 摘要 / 標籤 / entities / embedding 參考
8. `embed` 依據目前 metadata 產生並落地保存 embedding
9. `inspect` 可先檢查一張圖是否已經在庫裡
10. `upload` 把縮圖送到 Dropbox
11. `find` 用關鍵字找圖
12. `fetch` 把已記住的圖從 Dropbox 拉回本機

## OCR / analysis / embedding notes
- OCR 目前是可插拔：
  - 若系統有 `tesseract`，可用 `--auto-ocr`
  - 或在 local config 設 `ocr_command`
- 自動分析目前也是可插拔：
  - 可在 local config 設 `analysis_command`，回傳固定 JSON
  - 若未設定，就不做 CLI 端影像理解，只回退為空分析結果
- `entities` 目前固定 schema：
  - `dates`, `times`, `people`, `places`, `organizations`, `objects`
- Embedding 目前會直接呼叫 OpenAI `/v1/embeddings`
- 向量目前存本機 SQLite `photo_embeddings` 表

## Local-only config example
```json
{
  "db_path": "/absolute/path/to/iRemembo-local/photo-memory.db",
  "thumb_dir": "/absolute/path/to/iRemembo-local/thumbs",
  "dropbox_base": "/photo-memory",
  "dropbox_tool": "/absolute/path/to/iRemembo/scripts/dropbox_tool.py",
  "embedding_model": "text-embedding-3-small",
  "ocr_command": [],
  "analysis_command": []
}
```
