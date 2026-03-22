#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dropbox_path TEXT,
  sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  noted_at TEXT NOT NULL,
  user_note TEXT,
  summary TEXT,
  ocr_text TEXT,
  tags_json TEXT,
  entities_json TEXT,
  embedding_model TEXT,
  embedding_ref TEXT,
  status TEXT NOT NULL DEFAULT 'draft'
);
CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256);
CREATE INDEX IF NOT EXISTS idx_photos_status ON photos(status);

CREATE TABLE IF NOT EXISTS photo_embeddings (
  photo_id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  input_text TEXT NOT NULL,
  vector_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(photo_id) REFERENCES photos(id)
);
'''


def resolve_config_path(raw: str) -> Path:
    chosen = raw or os.environ.get('IREMEMBO_CONFIG', '')
    if not chosen:
        raise SystemExit('config path required: pass --config or set IREMEMBO_CONFIG')
    return Path(chosen).expanduser().resolve()


def load_config(path: Path) -> dict:
    with path.open() as f:
        cfg = json.load(f)
    required = ['db_path', 'thumb_dir', 'dropbox_base', 'dropbox_tool']
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise SystemExit(f'missing config keys: {", ".join(missing)}')
    return cfg


def ensure_db(cfg: dict):
    db_path = Path(cfg['db_path'])
    thumb_dir = Path(cfg['thumb_dir'])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_thumb(src: Path, thumb_dir: Path, sha: str) -> Path:
    ext = src.suffix.lower() or '.jpg'
    if ext not in {'.jpg', '.jpeg', '.png', '.webp'}:
        ext = '.jpg'
    out = thumb_dir / f'{sha[:16]}{ext}'
    if out.exists():
        return out
    cmd = ['ffmpeg', '-y', '-i', str(src), '-vf', 'scale=1280:-2', '-q:v', '4', str(out)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        shutil.copy2(src, out)
    return out


def parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or '').split(',') if t.strip()]


def build_entities(args) -> dict:
    return {
        'dates': args.dates or [],
        'times': getattr(args, 'times', []) or [],
        'people': args.people or [],
        'places': args.places or [],
        'organizations': getattr(args, 'organizations', []) or [],
        'objects': args.objects or [],
    }


def maybe_run_ocr(image_path: Path, cfg: dict) -> str:
    command = cfg.get('ocr_command', '')
    if command:
        cmd = [part.replace('{image}', str(image_path)) for part in command]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception:
            return ''
    tesseract = shutil.which('tesseract')
    if not tesseract:
        return ''
    try:
        result = subprocess.run(
            [tesseract, str(image_path), 'stdout', '-l', 'chi_tra+eng'],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ''


def normalize_analysis(raw: dict) -> dict:
    entities = raw.get('entities') or {}
    return {
        'summary': (raw.get('summary') or '').strip(),
        'tags': [str(t).strip() for t in (raw.get('tags') or []) if str(t).strip()],
        'entities': {
            'dates': [str(x).strip() for x in (entities.get('dates') or []) if str(x).strip()],
            'times': [str(x).strip() for x in (entities.get('times') or []) if str(x).strip()],
            'people': [str(x).strip() for x in (entities.get('people') or []) if str(x).strip()],
            'places': [str(x).strip() for x in (entities.get('places') or []) if str(x).strip()],
            'organizations': [str(x).strip() for x in (entities.get('organizations') or []) if str(x).strip()],
            'objects': [str(x).strip() for x in (entities.get('objects') or []) if str(x).strip()],
        },
        'ocr_text': (raw.get('ocr_text') or '').strip(),
    }


def run_analysis_command(image_path: Path, cfg: dict, ocr_text: str, user_note: str) -> dict:
    command = cfg.get('analysis_command', '')
    if not command:
        return {}
    cmd = [
        part.replace('{image}', str(image_path)).replace('{ocr_text}', ocr_text).replace('{note}', user_note or '')
        for part in command
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return normalize_analysis(json.loads(result.stdout))


def analyze_image(image_path: Path, cfg: dict, user_note: str = '', ocr_text: str = '') -> dict:
    try:
        via_cmd = run_analysis_command(image_path, cfg, ocr_text, user_note)
        if via_cmd:
            return via_cmd
    except Exception:
        pass
    return {'summary': '', 'tags': [], 'entities': {}, 'ocr_text': ocr_text or ''}


def build_embedding_input(summary: str, ocr_text: str, tags_json: str, entities_json: str, user_note: str) -> str:
    parts = []
    if summary:
        parts.append(f'summary: {summary}')
    if user_note:
        parts.append(f'note: {user_note}')
    if ocr_text:
        parts.append(f'ocr: {ocr_text}')
    try:
        tags = json.loads(tags_json or '[]')
    except Exception:
        tags = []
    if tags:
        parts.append('tags: ' + ', '.join(tags))
    try:
        entities = json.loads(entities_json or '{}')
    except Exception:
        entities = {}
    flat_entities = []
    for key, values in entities.items():
        if values:
            flat_entities.append(f"{key}: {', '.join(values)}")
    if flat_entities:
        parts.append('entities: ' + ' | '.join(flat_entities))
    return '\n'.join(parts).strip()


def create_embedding(text: str, model: str) -> tuple[str, list[float]]:
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        raise SystemExit('OPENAI_API_KEY is required for embeddings')
    payload = json.dumps({'model': model, 'input': text}).encode()
    req = urllib.request.Request(
        'https://api.openai.com/v1/embeddings',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        raise SystemExit(f'embedding request failed: {e.code} {msg}')
    vector = resp['data'][0]['embedding']
    return model, vector


def store_embedding(cfg: dict, photo_id: int, model: str, input_text: str, vector: list[float]):
    ensure_db(cfg)
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.execute(
            '''
            INSERT INTO photo_embeddings (photo_id, model, input_text, vector_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(photo_id) DO UPDATE SET
              model = excluded.model,
              input_text = excluded.input_text,
              vector_json = excluded.vector_json,
              created_at = excluded.created_at
            ''',
            (photo_id, model, input_text, json.dumps(vector), utc_now()),
        )
        conn.execute(
            'UPDATE photos SET embedding_model = ?, embedding_ref = ?, noted_at = ? WHERE id = ?',
            (model, f'sqlite:photo_embeddings:{photo_id}', utc_now(), photo_id),
        )
        conn.commit()


def prepare_photo_row(cfg: dict, args) -> dict:
    src = Path(args.image).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f'missing image: {src}')
    sha = sha256_file(src)
    thumb = make_thumb(src, Path(cfg['thumb_dir']), sha)
    return {
        'dropbox_path': args.dropbox_path or f"{cfg['dropbox_base']}/{thumb.name}",
        'sha256': sha,
        'created_at': datetime.fromtimestamp(src.stat().st_mtime, timezone.utc).isoformat(),
        'noted_at': utc_now(),
        'user_note': args.note,
        'summary': args.summary,
        'ocr_text': args.ocr_text,
        'tags_json': json.dumps(parse_tags(args.tags), ensure_ascii=False),
        'entities_json': json.dumps(build_entities(args), ensure_ascii=False),
        'embedding_model': args.embedding_model,
        'embedding_ref': args.embedding_ref,
        'status': args.status,
    }


def find_photo_by_sha(conn, sha: str):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        '''
        SELECT id, dropbox_path, sha256, created_at, noted_at,
               user_note, summary, ocr_text, tags_json, entities_json,
               embedding_model, embedding_ref, status
        FROM photos WHERE sha256 = ? ORDER BY id DESC LIMIT 1
        ''',
        (sha,),
    ).fetchone()


def insert_photo(conn, cfg: dict, args) -> dict:
    row = prepare_photo_row(cfg, args)
    cols = ', '.join(row.keys())
    qs = ', '.join('?' for _ in row)
    cur = conn.execute(f'INSERT INTO photos ({cols}) VALUES ({qs})', tuple(row.values()))
    conn.commit()
    return {'id': cur.lastrowid, **row}


def upload_photo(cfg: dict, photo_id: int, image_path: str, dropbox_path: str, status: str = 'uploaded'):
    src = Path(image_path).expanduser().resolve()
    sha = sha256_file(src)
    thumb = make_thumb(src, Path(cfg['thumb_dir']), sha)
    subprocess.run([
        sys.executable, cfg['dropbox_tool'], 'upload', str(thumb), dropbox_path, '--overwrite'
    ], check=True)
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.execute('UPDATE photos SET status = ?, noted_at = ? WHERE id = ?', (status, utc_now(), photo_id))
        conn.commit()


def maybe_embed_photo(cfg: dict, photo: dict, model_hint: str = '') -> dict:
    text = build_embedding_input(
        photo.get('summary', ''),
        photo.get('ocr_text', ''),
        photo.get('tags_json', ''),
        photo.get('entities_json', ''),
        photo.get('user_note', ''),
    )
    if not text:
        return photo
    model, vector = create_embedding(text, model_hint or cfg.get('embedding_model', 'text-embedding-3-small'))
    store_embedding(cfg, photo['id'], model, text, vector)
    photo = dict(photo)
    photo['embedding_model'] = model
    photo['embedding_ref'] = f'sqlite:photo_embeddings:{photo["id"]}'
    return photo


def cmd_init(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    print(cfg['db_path'])


def cmd_add(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    with sqlite3.connect(cfg['db_path']) as conn:
        prepared = prepare_photo_row(cfg, args)
        existing = find_photo_by_sha(conn, prepared['sha256'])
        if existing and args.dedup != 'allow-new':
            print(json.dumps({'dedup': True, 'record': dict(existing)}, ensure_ascii=False, indent=2))
            return
        row = insert_photo(conn, cfg, args)
    print(json.dumps(row, ensure_ascii=False, indent=2))


def cmd_list(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT id, noted_at, summary, dropbox_path, status FROM photos ORDER BY id DESC LIMIT ?',
            (args.limit,),
        ).fetchall()
    print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))


def cmd_find(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    q = f'%{args.query}%'
    sql = '''
    SELECT id, noted_at, summary, user_note, dropbox_path, tags_json, status
    FROM photos
    WHERE summary LIKE ? OR user_note LIKE ? OR ocr_text LIKE ? OR tags_json LIKE ? OR entities_json LIKE ?
    ORDER BY id DESC LIMIT ?
    '''
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (q, q, q, q, q, args.limit)).fetchall()
    print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))


def cmd_upload(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    raise SystemExit('upload by id is disabled in the current schema: use remember/remember-chat at ingest time')


def cmd_fetch(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT id, sha256, dropbox_path, summary, status FROM photos WHERE id = ?',
            (args.id,),
        ).fetchone()
        if not row:
            raise SystemExit(f'no photo id={args.id}')

    default_out = Path(cfg['thumb_dir']) / 'fetched' / Path(row['dropbox_path']).name
    out_path = Path(args.out).expanduser().resolve() if args.out else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, cfg['dropbox_tool'], 'download', row['dropbox_path'], str(out_path)
    ], check=True)
    print(json.dumps({
        'id': args.id,
        'summary': row['summary'],
        'dropbox_path': row['dropbox_path'],
        'local_path': str(out_path),
        'status': row['status'],
    }, ensure_ascii=False, indent=2))


def cmd_annotate(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    if args.auto_ocr:
        raise SystemExit('annotate --auto-ocr is disabled in the current schema: OCR should happen before write-in')
    ocr_text = args.ocr_text
    patch = {
        'noted_at': utc_now(),
        'summary': args.summary,
        'ocr_text': ocr_text,
        'tags_json': json.dumps(parse_tags(args.tags), ensure_ascii=False),
        'entities_json': json.dumps(build_entities(args), ensure_ascii=False),
        'embedding_model': args.embedding_model,
        'embedding_ref': args.embedding_ref,
        'status': args.status,
    }
    assignments = ', '.join(f'{k} = ?' for k in patch)
    values = list(patch.values()) + [args.id]
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute('SELECT id FROM photos WHERE id = ?', (args.id,)).fetchone()
        if not exists:
            raise SystemExit(f'no photo id={args.id}')
        conn.execute(f'UPDATE photos SET {assignments} WHERE id = ?', values)
        conn.commit()
        row = conn.execute(
            'SELECT id, noted_at, summary, ocr_text, tags_json, entities_json, embedding_model, embedding_ref, status FROM photos WHERE id = ?',
            (args.id,),
        ).fetchone()
    print(json.dumps(dict(row), ensure_ascii=False, indent=2))


def apply_analysis_to_args(args, analyzed: dict):
    if not args.summary:
        args.summary = analyzed.get('summary', '')
    if not args.tags:
        args.tags = ','.join(analyzed.get('tags', []))
    if not args.ocr_text:
        args.ocr_text = analyzed.get('ocr_text', '')
    entities = analyzed.get('entities', {})
    if not args.dates:
        args.dates = entities.get('dates', []) or []
    if not args.times:
        args.times = entities.get('times', []) or []
    if not args.people:
        args.people = entities.get('people', []) or []
    if not args.places:
        args.places = entities.get('places', []) or []
    if not args.organizations:
        args.organizations = entities.get('organizations', []) or []
    if not args.objects:
        args.objects = entities.get('objects', []) or []


def remember_prepared(args, cfg: dict):
    with sqlite3.connect(cfg['db_path']) as conn:
        prepared = prepare_photo_row(cfg, args)
        existing = find_photo_by_sha(conn, prepared['sha256'])
        if existing and args.dedup == 'return-existing':
            print(json.dumps({'dedup': True, 'record': dict(existing)}, ensure_ascii=False, indent=2))
            return
        row = insert_photo(conn, cfg, args)
    if args.auto_embed:
        row = maybe_embed_photo(cfg, row, model_hint=args.embedding_model)
    upload_photo(cfg, row['id'], args.image, row['dropbox_path'], status=args.final_status)
    row['status'] = args.final_status
    result = {
        'id': row['id'],
        'summary': row['summary'],
        'dropbox_path': row['dropbox_path'],
        'embedding_model': row['embedding_model'],
        'embedding_ref': row['embedding_ref'],
        'status': args.final_status,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_remember(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    image_path = Path(args.image).expanduser().resolve()
    if args.auto_ocr and not args.ocr_text:
        args.ocr_text = maybe_run_ocr(image_path, cfg)
    if args.auto_analyze:
        analyzed = analyze_image(image_path, cfg, user_note=args.note, ocr_text=args.ocr_text)
        apply_analysis_to_args(args, analyzed)
    remember_prepared(args, cfg)


def cmd_remember_chat(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    analyzed = normalize_analysis(json.loads(args.analysis_json))
    apply_analysis_to_args(args, analyzed)
    remember_prepared(args, cfg)


def cmd_embed(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT id, summary, ocr_text, tags_json, entities_json, user_note FROM photos WHERE id = ?',
            (args.id,),
        ).fetchone()
        if not row:
            raise SystemExit(f'no photo id={args.id}')
    text = build_embedding_input(row['summary'], row['ocr_text'], row['tags_json'], row['entities_json'], row['user_note'])
    if not text:
        raise SystemExit('nothing to embed: photo record has no summary/ocr/tags/entities/note')
    model, vector = create_embedding(text, args.model or cfg.get('embedding_model', 'text-embedding-3-small'))
    store_embedding(cfg, args.id, model, text, vector)
    print(json.dumps({
        'id': args.id,
        'model': model,
        'embedding_ref': f'sqlite:photo_embeddings:{args.id}',
        'dimensions': len(vector),
    }, ensure_ascii=False, indent=2))


def cmd_inspect(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    src = Path(args.image).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f'missing image: {src}')
    sha = sha256_file(src)
    with sqlite3.connect(cfg['db_path']) as conn:
        existing = find_photo_by_sha(conn, sha)
    out = {
        'image': str(src),
        'sha256': sha,
        'exists': bool(existing),
    }
    if existing:
        out['record'] = dict(existing)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def cmd_search(args):
    cfg = load_config(resolve_config_path(args.config))
    ensure_db(cfg)
    query_text = args.query.strip()
    if not query_text:
        raise SystemExit('query is required')

    q = f'%{query_text}%'
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            '''
            SELECT p.id, p.noted_at, p.summary, p.user_note, p.ocr_text, p.tags_json,
                   p.entities_json, p.dropbox_path, p.status,
                   e.model AS embedding_model, e.input_text, e.vector_json
            FROM photos p
            LEFT JOIN photo_embeddings e ON e.photo_id = p.id
            WHERE p.summary LIKE ? OR p.user_note LIKE ? OR p.ocr_text LIKE ? OR p.tags_json LIKE ? OR p.entities_json LIKE ? OR e.input_text LIKE ?
            ORDER BY p.id DESC
            ''',
            (q, q, q, q, q, q),
        ).fetchall()

    query_model = ''
    query_vector = []
    if args.semantic:
        query_model, query_vector = create_embedding(query_text, args.model or cfg.get('embedding_model', 'text-embedding-3-small'))

    scored = []
    for row in rows:
        item = dict(row)
        keyword_score = 0
        haystacks = [
            item.get('summary') or '',
            item.get('user_note') or '',
            item.get('ocr_text') or '',
            item.get('tags_json') or '',
            item.get('entities_json') or '',
            item.get('input_text') or '',
        ]
        q_lower = query_text.lower()
        for text in haystacks:
            if q_lower in text.lower():
                keyword_score += 1

        semantic_score = 0.0
        if query_vector and item.get('vector_json'):
            try:
                semantic_score = cosine_similarity(query_vector, json.loads(item['vector_json']))
            except Exception:
                semantic_score = 0.0

        final_score = keyword_score + semantic_score
        if keyword_score == 0 and semantic_score == 0:
            continue
        scored.append({
            'id': item['id'],
            'summary': item['summary'],
            'dropbox_path': item['dropbox_path'],
            'status': item['status'],
            'keyword_score': keyword_score,
            'semantic_score': round(semantic_score, 6),
            'score': round(final_score, 6),
            'embedding_model': item.get('embedding_model') or '',
        })

    scored.sort(key=lambda x: (x['score'], x['id']), reverse=True)
    result = {
        'query': query_text,
        'semantic': bool(args.semantic),
        'query_model': query_model,
        'results': scored[:args.limit],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser():
    p = argparse.ArgumentParser(description='iRemembo MVP')
    p.add_argument(
        '--config',
        default='',
        help='Path to local-only config JSON (or set IREMEMBO_CONFIG)',
    )
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('init')
    s.set_defaults(func=cmd_init)

    s = sub.add_parser('add')
    s.add_argument('image')
    s.add_argument('--note', default='')
    s.add_argument('--summary', default='')
    s.add_argument('--ocr-text', default='')
    s.add_argument('--tags', default='')
    s.add_argument('--dates', nargs='*', default=[])
    s.add_argument('--times', nargs='*', default=[])
    s.add_argument('--people', nargs='*', default=[])
    s.add_argument('--places', nargs='*', default=[])
    s.add_argument('--organizations', nargs='*', default=[])
    s.add_argument('--objects', nargs='*', default=[])
    s.add_argument('--embedding-model', default='')
    s.add_argument('--embedding-ref', default='')
    s.add_argument('--dropbox-path', default='')
    s.add_argument('--status', default='draft')
    s.add_argument('--dedup', choices=['return-existing', 'allow-new'], default='return-existing')
    s.set_defaults(func=cmd_add)

    s = sub.add_parser('remember')
    s.add_argument('image')
    s.add_argument('--note', default='')
    s.add_argument('--summary', default='')
    s.add_argument('--ocr-text', default='')
    s.add_argument('--tags', default='')
    s.add_argument('--dates', nargs='*', default=[])
    s.add_argument('--times', nargs='*', default=[])
    s.add_argument('--people', nargs='*', default=[])
    s.add_argument('--places', nargs='*', default=[])
    s.add_argument('--organizations', nargs='*', default=[])
    s.add_argument('--objects', nargs='*', default=[])
    s.add_argument('--embedding-model', default='')
    s.add_argument('--embedding-ref', default='')
    s.add_argument('--dropbox-path', default='')
    s.add_argument('--status', default='annotated')
    s.add_argument('--final-status', default='uploaded')
    s.add_argument('--auto-ocr', action='store_true')
    s.add_argument('--auto-analyze', action='store_true')
    s.add_argument('--auto-embed', action='store_true')
    s.add_argument('--dedup', choices=['return-existing', 'allow-new'], default='return-existing')
    s.set_defaults(func=cmd_remember)

    s = sub.add_parser('remember-chat')
    s.add_argument('image')
    s.add_argument('--analysis-json', required=True)
    s.add_argument('--note', default='')
    s.add_argument('--summary', default='')
    s.add_argument('--ocr-text', default='')
    s.add_argument('--tags', default='')
    s.add_argument('--dates', nargs='*', default=[])
    s.add_argument('--times', nargs='*', default=[])
    s.add_argument('--people', nargs='*', default=[])
    s.add_argument('--places', nargs='*', default=[])
    s.add_argument('--organizations', nargs='*', default=[])
    s.add_argument('--objects', nargs='*', default=[])
    s.add_argument('--embedding-model', default='')
    s.add_argument('--embedding-ref', default='')
    s.add_argument('--dropbox-path', default='')
    s.add_argument('--status', default='annotated')
    s.add_argument('--final-status', default='uploaded')
    s.add_argument('--auto-embed', action='store_true')
    s.add_argument('--dedup', choices=['return-existing', 'allow-new'], default='return-existing')
    s.set_defaults(func=cmd_remember_chat)

    s = sub.add_parser('list')
    s.add_argument('--limit', type=int, default=20)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser('find')
    s.add_argument('query')
    s.add_argument('--limit', type=int, default=10)
    s.set_defaults(func=cmd_find)

    s = sub.add_parser('search')
    s.add_argument('query')
    s.add_argument('--limit', type=int, default=10)
    s.add_argument('--semantic', action='store_true')
    s.add_argument('--model', default='')
    s.set_defaults(func=cmd_search)

    s = sub.add_parser('upload')
    s.add_argument('id', type=int)
    s.set_defaults(func=cmd_upload)

    s = sub.add_parser('fetch')
    s.add_argument('id', type=int)
    s.add_argument('--out', default='')
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser('annotate')
    s.add_argument('id', type=int)
    s.add_argument('--summary', default='')
    s.add_argument('--ocr-text', default='')
    s.add_argument('--tags', default='')
    s.add_argument('--dates', nargs='*', default=[])
    s.add_argument('--times', nargs='*', default=[])
    s.add_argument('--people', nargs='*', default=[])
    s.add_argument('--places', nargs='*', default=[])
    s.add_argument('--organizations', nargs='*', default=[])
    s.add_argument('--objects', nargs='*', default=[])
    s.add_argument('--embedding-model', default='')
    s.add_argument('--embedding-ref', default='')
    s.add_argument('--status', default='annotated')
    s.add_argument('--auto-ocr', action='store_true')
    s.set_defaults(func=cmd_annotate)

    s = sub.add_parser('embed')
    s.add_argument('id', type=int)
    s.add_argument('--model', default='')
    s.set_defaults(func=cmd_embed)

    s = sub.add_parser('inspect')
    s.add_argument('image')
    s.set_defaults(func=cmd_inspect)

    return p


if __name__ == '__main__':
    args = build_parser().parse_args()
    args.func(args)
