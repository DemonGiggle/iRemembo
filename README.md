# iRemembo

iRemembo is a lightweight photo-memory project.

It stores:
- a remembered image copy in Dropbox
- searchable metadata in a local SQLite database
- optional embeddings in a local embedding table

The intended primary workflow is interactive:
1. the user sends an image in chat
2. the user explicitly says to remember it
3. chat-side image understanding produces structured metadata
4. iRemembo writes that metadata into the local DB and uploads the retained image copy to Dropbox

## What the project does
- remembers only images explicitly marked to keep
- stores image identity by `dropbox_path`
- keeps summary / OCR text / tags / entities searchable locally
- supports optional embedding generation for later semantic search
- keeps secrets and runtime data outside the repo

## Repository layout
- `src/photo_memory.py` — main CLI
- `scripts/dropbox_tool.py` — Dropbox helper
- `config/example.local.json` — local config template
- `docs/architecture.md` — architecture overview
- `.env.example` — example environment variables

## Local-only data
Keep these outside the repo:
- real config
- SQLite DB
- tokens / secrets
- local thumbnails or caches
- logs / runtime state

## Setup
1. Copy `config/example.local.json` to a local-only location.
2. Fill in your local paths.
3. Either set environment variables, or place local-only config files at the default convention paths.
4. Initialize the database.

Example with environment variables:

```bash
export IREMEMBO_CONFIG=~/iRemembo-local/config.json
export DROPBOX_CONFIG=~/secrets/dropbox.json
python3 src/photo_memory.py init
```

Example with default local convention paths:

```bash
mkdir -p ~/.config/iremembo
cp config/example.local.json ~/.config/iremembo/config.json
# put your Dropbox secret JSON at ~/.config/iremembo/dropbox.json
python3 src/photo_memory.py init --config ~/.config/iremembo/config.json
```

## Main commands

```bash
python3 src/photo_memory.py init
python3 src/photo_memory.py remember-chat /path/to/image.jpg --analysis-json '{"summary":"示例","tags":["標籤1"],"entities":{"objects":["照片"]},"ocr_text":""}' --auto-embed
python3 scripts/remember_to_iremembo.py /path/to/image.jpg --analysis-json '{"summary":"示例","tags":["標籤1"],"entities":{"objects":["照片"]},"ocr_text":""}'
python3 src/photo_memory.py remember /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2" --auto-embed
python3 src/photo_memory.py add /path/to/image.jpg --summary "示例" --tags "標籤1,標籤2"
python3 src/photo_memory.py annotate 1 --summary "更新後摘要" --tags "標籤1,標籤2,標籤3"
python3 src/photo_memory.py embed 1
python3 src/photo_memory.py inspect /path/to/image.jpg
python3 src/photo_memory.py find 關鍵字
python3 src/photo_memory.py search 關鍵字
python3 src/photo_memory.py search 關鍵字 --semantic
python3 src/photo_memory.py fetch 1
```

## Data model
The `photos` table keeps:
- `dropbox_path`
- `sha256`
- `summary`
- `ocr_text`
- `tags_json`
- `entities_json`
- `user_note`
- timestamps
- status
- embedding reference fields

The actual embedding vector is stored in `photo_embeddings`.

## Notes
- CLI-side auto analysis is pluggable through `analysis_command`.
- CLI does not use OpenAI vision fallback.
- Embeddings currently use OpenAI `/v1/embeddings` when enabled.
- Duplicate detection is SHA-256 based.
- `remember` / `remember-chat` are atomic from the assistant's perspective: success means the DB row exists and the Dropbox file is present. If upload or later write steps fail, the command exits non-zero and compensates by cleaning up partial writes where possible.
- Retrieval now has two layers: `find` for plain keyword matching, and `search --semantic` for embedding-based ranking.
- `scripts/remember_to_iremembo.py` prefers explicit env vars, but if they are absent it will also look for local-only files at `~/.config/iremembo/config.json` and `~/.config/iremembo/dropbox.json`.

## Config example
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
