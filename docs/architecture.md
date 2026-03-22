# Architecture

## Pipeline
1. User explicitly says an image should be remembered
2. Analyze original image
3. Produce OCR text / summary / tags / entities
4. Create a smaller retained image
5. Store metadata in local SQLite
6. Upload retained image to Dropbox
7. Later, query local metadata first, then fetch image from Dropbox if needed

## Privacy boundary
- Repo contains no secrets or private user data
- Real config stays outside repo
- Tokens remain in local secret storage only
- SQLite DB and thumbnails remain local-only by default

## Current writeback boundary
- `remember` is the one-shot ingest path: create record, keep retained image, and upload to Dropbox
- `remember` / `add` now do SHA-256-based duplicate detection by default, so the same source image does not keep creating extra rows unless explicitly allowed
- `add` creates the initial record and retained image when a split workflow is preferred
- `annotate` is the explicit writeback point for OCR text, summary, tags, entities, and embedding references
- `inspect` is the lightweight preflight check for “is this image already remembered?”
- This keeps later vision/OCR pipelines replaceable without changing DB ownership flow

## OCR / analysis / embedding boundary
- OCR is pluggable: local config may define `ocr_command`, otherwise the app tries local `tesseract` if available
- Analysis is also pluggable: local config may define `analysis_command`
- If no `analysis_command` is configured, the app can try OpenAI vision via `/v1/chat/completions`
- Analysis output is normalized into fixed fields:
  - `summary`
  - `tags`
  - `entities.dates`
  - `entities.times`
  - `entities.people`
  - `entities.places`
  - `entities.organizations`
  - `entities.objects`
  - `ocr_text`
- Embeddings are generated from summary + note + OCR + tags + entities text
- Embedding vectors are stored locally in SQLite so repo stays clean

## Future work
- Better OCR engine installation / benchmarking
- Improve duplicate policy beyond exact SHA match
- Embedding-based retrieval ranking
- Tooling to fetch remembered images from Dropbox back to local/send-back flows
- Alternate storage backends
