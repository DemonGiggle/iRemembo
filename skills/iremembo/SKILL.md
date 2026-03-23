---
name: iremembo
description: Store and retrieve explicitly remembered images through the iRemembo project. Use when the user explicitly asks to remember an image or image-based fact, or when the user asks to bring back a previously remembered image/item. Favor direct recall-and-send behavior for clear photo lookup requests; avoid verbose intermediate narration unless there is ambiguity or failure.
---

Use this skill as a thin bridge from OpenClaw chat flow into the iRemembo CLI.

## Core rules

- Write only when the user explicitly asks to remember/store an image.
- Do not query iRemembo on every turn; use it only when the conversation suggests recall of a previously remembered image/item.
- Treat chat-side image understanding and iRemembo persistence as separate steps.
- Prefer `remember-chat` when the chat already has structured analysis.
- When returning an image to chat, fetch/recall it to a safe local path first, then send it with the messaging tool.
- If the user clearly wants the image itself back, prefer direct retrieval over explanatory chatter.
- If recall yields a single strong match, fetch and send it directly.
- Only stop to ask the user when there is no convincing match or multiple plausible matches that truly need disambiguation.

## Project assumptions

- Repo root: `/home/gigo/.openclaw/projects/iRemembo`
- Local config comes from `IREMEMBO_CONFIG`
- Dropbox secrets come from `DROPBOX_CONFIG`
- Safe temporary send path: `/home/gigo/.openclaw/workspace/tmp/iremembo-send/`

## Write flow

If the user explicitly wants to remember an image:

1. Understand the image in chat and prepare structured fields:
   - `summary`
   - `tags`
   - `entities`
   - `ocr_text`
2. Run `remember-chat`.
3. Prefer `--auto-embed` unless there is a reason not to generate embeddings.
4. Tell the user the memory was stored and mention the key summary briefly.

Example:

```bash
python3 src/photo_memory.py remember-chat /path/to/image.jpg \
  --analysis-json '{"summary":"...","tags":["..."],"entities":{"objects":["..."]},"ocr_text":"..."}' \
  --note 'user said this should be remembered' \
  --auto-embed
```

## Search flow

If the user asks about a previously remembered item/image:

1. Decide whether iRemembo is actually relevant.
2. Start with keyword search when the user gives concrete words.
3. Use semantic search when the request is conceptual, fuzzy, or based on meaning.
4. If the user clearly wants the photo back, optimize for getting to a confident match quickly instead of narrating each step.
5. Only summarize candidates before fetching when multiple realistic matches exist.

Examples:

```bash
python3 src/photo_memory.py find 關鍵字
python3 src/photo_memory.py search 關鍵字
python3 src/photo_memory.py search 關鍵字 --semantic
```

## Recall/send flow

If the user wants the image itself back:

1. Prefer one-step recall to a safe output path.
2. If needed, do search first and fetch by id second.
3. Send only from a safe local path that OpenClaw messaging accepts.
4. Do not try to send arbitrary absolute paths outside allowed media areas.
5. If there is exactly one convincing match, send the image directly and keep any text short.
6. Avoid verbose progress updates unless something is blocked, ambiguous, or failed.

Examples:

```bash
python3 src/photo_memory.py recall "Brandon Sanderson" --semantic \
  --out /home/gigo/.openclaw/workspace/tmp/iremembo-send/result.jpg

python3 src/photo_memory.py fetch 2 \
  --out /home/gigo/.openclaw/workspace/tmp/iremembo-send/result.jpg
```

## Response style for recall

- Good default for a confirmed match: send the image with a short caption like `找到了，這張就是 Shelly。`
- Do not explain search strategy, Dropbox mechanics, or intermediate inspection unless the user asks.
- Do not list candidate items when only one confident match exists.
- If no match exists, say so plainly.
- If multiple matches exist, present the shortest useful disambiguation prompt possible.

## Failure handling

- If `remember-chat` fails before upload, report partial state clearly instead of pretending the flow completed.
- If Dropbox env/config is missing, fix config first rather than retrying blindly.
- If messaging rejects a local file path, fetch/recall to the safe send directory and send from there.
- If search returns no match, say so plainly instead of guessing.
- If multiple matches are similarly plausible, ask a short narrowing question rather than dumping all internal steps.

## Reference

Read `references/commands.md` for command patterns, JSON shape, and common failure cases.
