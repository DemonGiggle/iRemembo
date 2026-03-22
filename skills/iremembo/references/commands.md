# iRemembo command reference

## Environment

Set these before running the CLI:

```bash
export IREMEMBO_CONFIG=/path/to/local/config.json
export DROPBOX_CONFIG=/path/to/dropbox.json
```

## remember-chat

Use when chat has already produced structured image understanding.

```bash
python3 src/photo_memory.py remember-chat /path/to/image.jpg \
  --analysis-json '<json>' \
  --note 'optional note' \
  --auto-embed
```

### Expected analysis JSON shape

```json
{
  "summary": "一句摘要",
  "tags": ["標籤1", "標籤2"],
  "entities": {
    "dates": [],
    "times": [],
    "people": [],
    "places": [],
    "organizations": [],
    "objects": []
  },
  "ocr_text": "可留空"
}
```

Notes:
- Missing fields should be supplied as empty arrays/empty string where practical.
- Keep tags concise.
- Prefer concrete nouns in `objects`.

## Search commands

### Plain keyword matching

```bash
python3 src/photo_memory.py find 關鍵字
python3 src/photo_memory.py search 關鍵字
```

### Semantic ranking

```bash
python3 src/photo_memory.py search 關鍵字 --semantic
```

Use semantic search when the user describes the thing indirectly rather than by exact visible text.

## Fetch / recall

### Fetch by id

```bash
python3 src/photo_memory.py fetch 2 \
  --out /home/gigo/.openclaw/workspace/tmp/iremembo-send/result.jpg
```

### Search then fetch in one step

```bash
python3 src/photo_memory.py recall "查詢詞" --semantic \
  --out /home/gigo/.openclaw/workspace/tmp/iremembo-send/result.jpg
```

## Safe send rule

When returning an image to chat, prefer a safe temporary path such as:

```text
/home/gigo/.openclaw/workspace/tmp/iremembo-send/
```

Then send it through the messaging tool.

## Common failure cases

### `DROPBOX_CONFIG` missing
Meaning: Dropbox helper cannot authenticate.
Action: set the environment variable correctly and retry.

### `--analysis-json` missing
Meaning: `remember-chat` cannot run without structured chat-side analysis.
Action: build the JSON first, then rerun.

### Search returns no match
Meaning: no convincing remembered record matched the query.
Action: report no match; optionally try a more concrete query.

### Messaging rejects local path
Meaning: the file is outside allowed media directories.
Action: fetch/recall into the safe send directory, then send from there.

### Dedup returns an existing row
Meaning: the same image hash is already in the DB.
Action: inspect the returned record; do not assume a new row was created.
