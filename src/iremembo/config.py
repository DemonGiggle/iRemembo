import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
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


def file_mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


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
