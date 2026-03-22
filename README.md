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
python3 src/photo_memory.py add /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2"
python3 src/photo_memory.py find 關鍵字
python3 src/photo_memory.py upload 1
python3 src/photo_memory.py fetch 1
```

## Current useful flow
1. `add` 建立本機索引紀錄
2. `upload` 把縮圖送到 Dropbox
3. `find` 用關鍵字找圖
4. `fetch` 把已記住的圖從 Dropbox 拉回本機

## Local-only config example
```json
{
  "db_path": "/absolute/path/to/iRemembo-local/photo-memory.db",
  "thumb_dir": "/absolute/path/to/iRemembo-local/thumbs",
  "dropbox_base": "/photo-memory",
  "dropbox_tool": "/absolute/path/to/iRemembo/scripts/dropbox_tool.py"
}
```
