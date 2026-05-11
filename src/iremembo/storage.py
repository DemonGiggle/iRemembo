import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from .analysis import build_embedding_input, build_entities, cosine_similarity, create_embedding, parse_tags
from .config import ensure_db, file_mtime_utc, make_thumb, sha256_file, utc_now


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
        'created_at': file_mtime_utc(src),
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


def run_dropbox_tool(cfg: dict, *tool_args: str, capture_output: bool = True):
    return subprocess.run(
        [sys.executable, cfg['dropbox_tool'], *tool_args],
        check=True,
        text=True,
        capture_output=capture_output,
    )


def upload_thumb(cfg: dict, thumb_path: Path, dropbox_path: str):
    run_dropbox_tool(cfg, 'upload', str(thumb_path), dropbox_path, '--overwrite')


def dropbox_path_exists(cfg: dict, dropbox_path: str) -> bool:
    try:
        run_dropbox_tool(cfg, 'stat', dropbox_path, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False


def delete_dropbox_path(cfg: dict, dropbox_path: str):
    try:
        run_dropbox_tool(cfg, 'delete', dropbox_path, capture_output=True)
    except subprocess.CalledProcessError:
        pass


def finalize_photo_status(cfg: dict, photo_id: int, status: str):
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.execute('UPDATE photos SET status = ?, noted_at = ? WHERE id = ?', (status, utc_now(), photo_id))
        conn.commit()


def set_photo_embedding_fields(cfg: dict, photo_id: int, model: str, embedding_ref: str):
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.execute(
            'UPDATE photos SET embedding_model = ?, embedding_ref = ?, noted_at = ? WHERE id = ?',
            (model, embedding_ref, utc_now(), photo_id),
        )
        conn.commit()


def delete_photo_record(cfg: dict, photo_id: int):
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.execute('DELETE FROM photo_embeddings WHERE photo_id = ?', (photo_id,))
        conn.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
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


def remember_prepared(args, cfg: dict):
    prepared = prepare_photo_row(cfg, args)
    image_path = Path(args.image).expanduser().resolve()
    thumb_path = make_thumb(image_path, Path(cfg['thumb_dir']), prepared['sha256'])

    with sqlite3.connect(cfg['db_path']) as conn:
        existing = find_photo_by_sha(conn, prepared['sha256'])

    if existing and args.dedup == 'return-existing':
        row = dict(existing)
        repaired = False
        if not dropbox_path_exists(cfg, row['dropbox_path']):
            upload_thumb(cfg, thumb_path, row['dropbox_path'])
            repaired = True
        if row.get('status') != args.final_status or repaired:
            finalize_photo_status(cfg, row['id'], args.final_status)
            row['status'] = args.final_status
        return {
            'id': row['id'],
            'summary': row.get('summary', ''),
            'dropbox_path': row['dropbox_path'],
            'embedding_model': row.get('embedding_model', ''),
            'embedding_ref': row.get('embedding_ref', ''),
            'status': row['status'],
            'dedup': True,
            'repaired_dropbox': repaired,
        }

    row = None
    uploaded = False
    try:
        upload_thumb(cfg, thumb_path, prepared['dropbox_path'])
        uploaded = True
        with sqlite3.connect(cfg['db_path']) as conn:
            cols = ', '.join(prepared.keys())
            qs = ', '.join('?' for _ in prepared)
            cur = conn.execute(f'INSERT INTO photos ({cols}) VALUES ({qs})', tuple(prepared.values()))
            conn.commit()
            row = {'id': cur.lastrowid, **prepared}
        if args.auto_embed:
            row = maybe_embed_photo(cfg, row, model_hint=args.embedding_model)
            if row.get('embedding_model') or row.get('embedding_ref'):
                set_photo_embedding_fields(cfg, row['id'], row.get('embedding_model', ''), row.get('embedding_ref', ''))
        finalize_photo_status(cfg, row['id'], args.final_status)
        row['status'] = args.final_status
    except Exception as e:
        if row and row.get('id'):
            delete_photo_record(cfg, row['id'])
        if uploaded:
            delete_dropbox_path(cfg, prepared['dropbox_path'])
        raise SystemExit(f'atomic remember failed: {e}')

    return {
        'id': row['id'],
        'summary': row['summary'],
        'dropbox_path': row['dropbox_path'],
        'embedding_model': row.get('embedding_model', ''),
        'embedding_ref': row.get('embedding_ref', ''),
        'status': args.final_status,
        'dedup': False,
    }


def list_photos(cfg: dict, limit: int) -> list[dict]:
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT id, noted_at, summary, dropbox_path, status FROM photos ORDER BY id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def find_photos(cfg: dict, query: str, limit: int) -> list[dict]:
    q = f'%{query}%'
    sql = '''
    SELECT id, noted_at, summary, user_note, dropbox_path, tags_json, status
    FROM photos
    WHERE summary LIKE ? OR user_note LIKE ? OR ocr_text LIKE ? OR tags_json LIKE ? OR entities_json LIKE ?
    ORDER BY id DESC LIMIT ?
    '''
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (q, q, q, q, q, limit)).fetchall()
    return [dict(r) for r in rows]


def fetch_photo_to_path(cfg: dict, photo_id: int, out: str = '') -> dict:
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT id, sha256, dropbox_path, summary, status FROM photos WHERE id = ?',
            (photo_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f'no photo id={photo_id}')

    default_out = Path(cfg['thumb_dir']) / 'fetched' / Path(row['dropbox_path']).name
    out_path = Path(out).expanduser().resolve() if out else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_dropbox_tool(cfg, 'download', row['dropbox_path'], str(out_path))
    return {
        'id': row['id'],
        'summary': row['summary'],
        'dropbox_path': row['dropbox_path'],
        'local_path': str(out_path),
        'status': row['status'],
    }


def annotate_photo(cfg: dict, args) -> dict:
    patch = {
        'noted_at': utc_now(),
        'summary': args.summary,
        'ocr_text': args.ocr_text,
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
    return dict(row)


def inspect_photo(cfg: dict, image: str) -> dict:
    src = Path(image).expanduser().resolve()
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
    return out


def embed_photo(cfg: dict, photo_id: int, model_hint: str = '') -> dict:
    with sqlite3.connect(cfg['db_path']) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT id, summary, ocr_text, tags_json, entities_json, user_note FROM photos WHERE id = ?',
            (photo_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f'no photo id={photo_id}')
    text = build_embedding_input(row['summary'], row['ocr_text'], row['tags_json'], row['entities_json'], row['user_note'])
    if not text:
        raise SystemExit('nothing to embed: photo record has no summary/ocr/tags/entities/note')
    model, vector = create_embedding(text, model_hint or cfg.get('embedding_model', 'text-embedding-3-small'))
    store_embedding(cfg, photo_id, model, text, vector)
    return {
        'id': photo_id,
        'model': model,
        'embedding_ref': f'sqlite:photo_embeddings:{photo_id}',
        'dimensions': len(vector),
    }


def run_search(cfg: dict, query_text: str, semantic: bool = False, model: str = '', limit: int = 10) -> dict:
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
    if semantic:
        query_model, query_vector = create_embedding(query_text, model or cfg.get('embedding_model', 'text-embedding-3-small'))

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
    return {
        'query': query_text,
        'semantic': bool(semantic),
        'query_model': query_model,
        'results': scored[:limit],
    }
