import json
import math
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


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
        raise RuntimeError('OPENAI_API_KEY is required for embeddings')
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
        raise RuntimeError(f'embedding request failed: {e.code} {msg}')
    vector = resp['data'][0]['embedding']
    return model, vector


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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
