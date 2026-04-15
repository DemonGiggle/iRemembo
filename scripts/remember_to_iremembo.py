#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from json import JSONDecoder
from pathlib import Path

DEFAULT_IREMEMBO_CONFIG = Path.home() / '.config' / 'iremembo' / 'config.json'
DEFAULT_DROPBOX_CONFIG = Path.home() / '.config' / 'iremembo' / 'dropbox.json'


def fail(msg: str, code: int = 1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(code)


def resolve_required_file(path_str: str, env_name: str, default_path: Path | None = None) -> Path:
    raw = path_str or os.environ.get(env_name, '')
    if raw:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            fail(f'{env_name} not found: {path}')
        return path
    if default_path and default_path.exists():
        os.environ[env_name] = str(default_path)
        return default_path
    fail(f'{env_name} is required')


def resolve_image(path_str: str) -> Path:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        fail(f'image not found: {path}')
    return path


def build_analysis(args) -> dict:
    if args.analysis_json:
        try:
            loaded = json.loads(args.analysis_json)
        except json.JSONDecodeError as e:
            fail(f'invalid --analysis-json: {e}')
    else:
        loaded = {}
    entities = loaded.get('entities') or {}
    analysis = {
        'summary': args.summary or loaded.get('summary') or '',
        'tags': loaded.get('tags') or [],
        'entities': {
            'dates': entities.get('dates') or [],
            'times': entities.get('times') or [],
            'people': entities.get('people') or [],
            'places': entities.get('places') or [],
            'organizations': entities.get('organizations') or [],
            'objects': entities.get('objects') or [],
        },
        'ocr_text': args.ocr_text or loaded.get('ocr_text') or '',
    }
    if args.tags:
        analysis['tags'] = [t.strip() for t in args.tags.split(',') if t.strip()]
    for field in ['dates', 'times', 'people', 'places', 'organizations', 'objects']:
        cli_val = getattr(args, field)
        if cli_val:
            analysis['entities'][field] = cli_val
    if not analysis['summary']:
        fail('summary is required (pass --summary or include it in --analysis-json)')
    return analysis


def parse_json_stream(stdout: str) -> list:
    decoder = JSONDecoder()
    idx = 0
    length = len(stdout)
    payloads = []
    while idx < length:
        while idx < length and stdout[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, next_idx = decoder.raw_decode(stdout, idx)
        except json.JSONDecodeError:
            break
        payloads.append(obj)
        idx = next_idx
    return payloads


def run_cli(repo_root: Path, image: Path, analysis: dict, note: str, dedup: str, auto_embed: bool) -> dict:
    cmd = [
        sys.executable,
        str(repo_root / 'src' / 'photo_memory.py'),
        'remember-chat',
        str(image),
        '--analysis-json', json.dumps(analysis, ensure_ascii=False),
        '--note', note,
        '--dedup', dedup,
    ]
    if auto_embed:
        cmd.append('--auto-embed')
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(result.stderr.strip() or result.stdout.strip() or 'remember-chat failed')

    payloads = parse_json_stream(result.stdout)
    if not payloads:
        fail(f'unexpected remember-chat output: {result.stdout.strip()}')

    final_payload = payloads[-1]
    if not isinstance(final_payload, dict):
        fail(f'unexpected remember-chat payload: {final_payload!r}')
    if final_payload.get('status') != 'uploaded':
        fail(f"remember-chat did not finish durably: {json.dumps(final_payload, ensure_ascii=False)}")
    if not final_payload.get('dropbox_path'):
        fail(f"remember-chat returned no dropbox_path: {json.dumps(final_payload, ensure_ascii=False)}")
    if not final_payload.get('id'):
        fail(f"remember-chat returned no record id: {json.dumps(final_payload, ensure_ascii=False)}")
    return {
        'payload_count': len(payloads),
        'final': final_payload,
        'events': payloads[:-1],
        'command': cmd,
    }


def main():
    p = argparse.ArgumentParser(description='Single-entry wrapper for remembering an image into iRemembo')
    p.add_argument('image')
    p.add_argument('--summary', default='')
    p.add_argument('--analysis-json', default='')
    p.add_argument('--ocr-text', default='')
    p.add_argument('--tags', default='')
    p.add_argument('--dates', nargs='*', default=[])
    p.add_argument('--times', nargs='*', default=[])
    p.add_argument('--people', nargs='*', default=[])
    p.add_argument('--places', nargs='*', default=[])
    p.add_argument('--organizations', nargs='*', default=[])
    p.add_argument('--objects', nargs='*', default=[])
    p.add_argument('--note', default='')
    p.add_argument('--dedup', choices=['return-existing', 'allow-new'], default='return-existing')
    p.add_argument('--no-auto-embed', action='store_true')
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    resolve_required_file('', 'IREMEMBO_CONFIG', DEFAULT_IREMEMBO_CONFIG)
    resolve_required_file('', 'DROPBOX_CONFIG', DEFAULT_DROPBOX_CONFIG)
    image = resolve_image(args.image)
    analysis = build_analysis(args)
    payload = run_cli(
        repo_root=repo_root,
        image=image,
        analysis=analysis,
        note=args.note,
        dedup=args.dedup,
        auto_embed=not args.no_auto_embed,
    )
    final_payload = payload['final']
    print(json.dumps({
        'ok': True,
        'image': str(image),
        'summary': analysis['summary'],
        'id': final_payload['id'],
        'dropbox_path': final_payload['dropbox_path'],
        'status': final_payload['status'],
        'dedup': bool(final_payload.get('dedup')),
        'repaired_dropbox': bool(final_payload.get('repaired_dropbox')),
        'embedding_model': final_payload.get('embedding_model', ''),
        'embedding_ref': final_payload.get('embedding_ref', ''),
        'result': payload,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
