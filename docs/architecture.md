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
- `remember-chat` is now the intended interactive ingest path: chat-side image understanding happens first, then fixed analysis JSON is written into iRemembo
- `remember` remains the CLI ingest path for local/scripted use
- `remember` / `remember-chat` / `add` do SHA-256-based duplicate detection by default, so the same source image does not keep creating extra rows unless explicitly allowed
- `remember` and `remember-chat` now use a compensate-on-failure flow so success implies both durable DB state and Dropbox presence. Duplicate hits also verify Dropbox presence and repair it before returning success.
- The DB keeps Dropbox identity + metadata, not local source/thumb paths; local thumbnails are treated as temporary implementation detail
- `add` creates the initial record when a split workflow is preferred
- `annotate` is the explicit writeback point for OCR text, summary, tags, entities, and embedding references
- `inspect` is the lightweight preflight check for “is this image already remembered?”
- This keeps later vision/OCR pipelines replaceable without changing DB ownership flow

## OCR / analysis / embedding boundary
- OCR is pluggable: local config may define `ocr_command`, otherwise the app tries local `tesseract` if available
- Analysis is also pluggable: local config may define `analysis_command`
- If no `analysis_command` is configured, CLI-side image understanding is skipped
- Interactive chat-side image understanding is the preferred path for remembered photos; `remember-chat` is the writeback bridge into the repo flow
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

## Retrieval
- `find` is the simple keyword-first lookup path over stored metadata text
- `search --semantic` embeds the query text, compares it against locally stored vectors, and returns ranked candidates
- Retrieval should query local DB first, then fetch from Dropbox only when the user wants the image back

## Future work
- Harden the interactive chat-driven ingest path now that it is wired
- Better OCR engine installation / benchmarking
- Improve duplicate policy beyond exact SHA match
- Better hybrid retrieval weighting / reranking
- Tooling to fetch remembered images from Dropbox back to local/send-back flows
- Alternate storage backends
