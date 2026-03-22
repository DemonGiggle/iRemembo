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
python3 src/photo_memory.py remember /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2" --auto-embed
python3 src/photo_memory.py add /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2"
python3 src/photo_memory.py annotate 1 --summary "更新後摘要" --tags "標籤1,標籤2,標籤3"
python3 src/photo_memory.py embed 1
python3 src/photo_memory.py find 關鍵字
python3 src/photo_memory.py upload 1
python3 src/photo_memory.py fetch 1
```

## Current useful flow
1. 最簡單：直接用 `remember` 一次完成建檔＋metadata＋上傳 Dropbox
2. 若要同時跑向量：`remember ... --auto-embed`
3. 若要拆步驟：`add` 建立本機索引紀錄
4. `annotate` 寫回 OCR / 摘要 / 標籤 / entities / embedding 參考
5. `embed` 依據目前 metadata 產生並落地保存 embedding
6. `upload` 把縮圖送到 Dropbox
7. `find` 用關鍵字找圖
8. `fetch` 把已記住的圖從 Dropbox 拉回本機

## OCR / embedding notes
- OCR 目前是可插拔：
  - 若系統有 `tesseract`，可用 `--auto-ocr`
  - 或在 local config 設 `ocr_command`
- Embedding 目前會直接呼叫 OpenAI `/v1/embeddings`
- 向量目前存本機 SQLite `photo_embeddings` 表

## Local-only config example
```json
{
  "db_path": "/absolute/path/to/iRemembo-local/photo-memory.db",
  "thumb_dir": "/absolute/path/to/iRemembo-local/thumbs",
  "dropbox_base": "/photo-memory",
  "dropbox_tool": "/absolute/path/to/iRemembo/scripts/dropbox_tool.py"
}
```
