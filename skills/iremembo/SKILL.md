---
name: iremembo
description: Store and retrieve explicitly remembered images through the iRemembo project. Trigger this whenever the user sends a photo and explicitly says to remember it, store it, keep it for later, or says phrases like 「記住 / 幫我記 / 記到 iRemembo / 之後要找這張 / 之後要翻給小孩看」. Also use it when the user asks to bring back a previously remembered image/item.
---

Use this skill as a thin bridge from OpenClaw chat flow into the iRemembo CLI.

Verification marker: workspace-skill-iremembo-loaded-2026-03-27.

## Hard trigger rule

If the current turn includes an image and the user explicitly indicates they want it remembered, you MUST route the write through iRemembo first.

Do **not** treat plain chat memory, `memory/YYYY-MM-DD.md`, or `MEMORY.md` as a substitute for iRemembo persistence in that case.

Examples that should trigger this skill:
- 「記住」
- 「幫我記」
- 「記到 iRemembo」
- 「之後要找這張」
- 「之後要翻給小孩看」
- any equivalent wording that clearly means the image itself should be kept/retrievable later

## Core rules

- Write only when the user explicitly asks to remember/store an image.
- Do not query iRemembo on every turn; use it only when the conversation suggests recall of a previously remembered image/item.
- Treat chat-side image understanding and iRemembo persistence as separate steps.
- Prefer the single-entry wrapper `scripts/remember_to_iremembo.py` for write/ingest flows.
- `memory/YYYY-MM-DD.md` may be updated as a supporting note, but only **after** iRemembo write success or after clearly reporting a partial failure.
- Never tell the user `記好了` unless the iRemembo command actually succeeded.
- If the user clearly wants the image itself back, prefer direct retrieval over explanatory chatter.
- If recall yields a single strong match, fetch and send it directly.
- Only stop to ask the user when there is no convincing match or multiple plausible matches that truly need disambiguation.

## Project assumptions

- Repo root: `/home/gigo/.openclaw/projects/iRemembo`
- Local config normally comes from `IREMEMBO_CONFIG`
- Dropbox secrets normally come from `DROPBOX_CONFIG`
- Wrapper fallback convention for local-only setups: `~/.config/iremembo/config.json` and `~/.config/iremembo/dropbox.json`
- Safe temporary send path: `/home/gigo/.openclaw/workspace/tmp/iremembo-send/`

## Write flow

If the user explicitly wants to remember an image:

1. Understand the image in chat and prepare structured fields:
   - `summary`
   - `tags`
   - `entities`
   - `ocr_text`
2. Run the single-entry wrapper:

```bash
python3 scripts/remember_to_iremembo.py /path/to/image.jpg \
  --analysis-json '{"summary":"...","tags":["..."],"entities":{"objects":["..."]},"ocr_text":"..."}' \
  --note 'user explicitly asked to remember this image'
```

3. Default to embedding generation; only disable it for a concrete reason.
4. Confirm success to the user only after the command succeeds and returns structured output.
5. If the write fails before upload/persistence, report partial state clearly instead of pretending completion.

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

- If the wrapper or `remember-chat` fails before upload, report partial state clearly instead of pretending the flow completed.
- If `IREMEMBO_CONFIG` or `DROPBOX_CONFIG` is missing, first check the wrapper fallback convention paths `~/.config/iremembo/config.json` and `~/.config/iremembo/dropbox.json`, then fix config rather than retrying blindly.
- If messaging rejects a local file path, fetch/recall to the safe send directory and send from there.
- If search returns no match, say so plainly instead of guessing.
- If multiple matches are similarly plausible, ask a short narrowing question rather than dumping all internal steps.

## Reference

Read `references/commands.md` for command patterns, JSON shape, and common failure cases.
