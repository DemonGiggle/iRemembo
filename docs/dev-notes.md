# iRemembo Dev Notes

This file holds implementation notes that do not belong in the public-facing README.

## Current product direction
- Primary workflow is interactive chat-driven remember flow.
- User must explicitly say to remember an image.
- Repo should stay safe to publish.
- Local config, DB, tokens, and runtime data must stay outside the repo.

## Current implementation notes
- `remember-chat` is the main bridge from chat-side analysis into the local DB.
- `remember` remains available for local/scripted use.
- CLI auto-analysis only uses `analysis_command` if configured.
- No CLI OpenAI vision fallback.
- Embeddings are optional and stored in local SQLite table `photo_embeddings`.
- Duplicate detection is SHA-256 based.
- Current schema keeps Dropbox path and metadata only; local source/thumb paths are not stored in DB.

## Dev-mode assumptions
- Fail-fast is preferred during development.
- Remember flows now compensate on failure: upload is part of success, and duplicate rows are revalidated against Dropbox before the CLI reports success.
- Resetting the dev DB is acceptable while iterating.
