import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .analysis import analyze_image, apply_analysis_to_args, maybe_run_ocr, normalize_analysis
from .config import ensure_db, load_config, resolve_config_path
from .doctor import run_doctor
from .storage import (
    annotate_photo,
    embed_photo,
    fetch_photo_to_path,
    find_photo_by_sha,
    find_photos,
    insert_photo,
    inspect_photo,
    list_photos,
    prepare_photo_row,
    remember_prepared,
    run_search,
)

PLUGIN_COMMANDS = {'doctor', 'fetch', 'find', 'inspect', 'recall', 'remember', 'remember-chat', 'search'}


def load_runtime_config(config_arg: str) -> dict:
    cfg = load_config(resolve_config_path(config_arg))
    ensure_db(cfg)
    return cfg


def plugin_success(command: str, payload: dict) -> dict:
    return {
        'ok': True,
        'command': command,
        **payload,
    }


def emit_plugin_error(command: str, error: str, code: int = 1):
    print(json.dumps({
        'ok': False,
        'command': command,
        'error': error,
    }, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(code)


def cmd_init(args):
    cfg = load_runtime_config(args.config)
    print(cfg['db_path'])


def cmd_add(args):
    cfg = load_runtime_config(args.config)
    with sqlite3.connect(cfg['db_path']) as conn:
        prepared = prepare_photo_row(cfg, args)
        existing = find_photo_by_sha(conn, prepared['sha256'])
        if existing and args.dedup != 'allow-new':
            print(json.dumps({'dedup': True, 'record': dict(existing)}, ensure_ascii=False, indent=2))
            return
        row = insert_photo(conn, cfg, args)
    print(json.dumps(row, ensure_ascii=False, indent=2))


def cmd_list(args):
    cfg = load_runtime_config(args.config)
    print(json.dumps(list_photos(cfg, args.limit), ensure_ascii=False, indent=2))


def cmd_find(args):
    cfg = load_runtime_config(args.config)
    return plugin_success('find', {
        'query': args.query,
        'results': find_photos(cfg, args.query, args.limit),
    })


def cmd_upload(args):
    load_runtime_config(args.config)
    raise SystemExit('upload by id is disabled in the current schema: use remember/remember-chat at ingest time')


def cmd_fetch(args):
    cfg = load_runtime_config(args.config)
    return plugin_success('fetch', fetch_photo_to_path(cfg, args.id, args.out))


def cmd_recall(args):
    cfg = load_runtime_config(args.config)
    result = run_search(cfg, args.query.strip(), semantic=args.semantic, model=args.model, limit=args.limit)
    if not result['results']:
        return plugin_success('recall', {
            'query': result['query'],
            'semantic': result['semantic'],
            'query_model': result['query_model'],
            'match': None,
        })
    best = result['results'][0]
    fetched = fetch_photo_to_path(cfg, best['id'], args.out)
    return plugin_success('recall', {
        'query': result['query'],
        'semantic': result['semantic'],
        'query_model': result['query_model'],
        'match': best,
        'fetched': fetched,
    })


def cmd_annotate(args):
    cfg = load_runtime_config(args.config)
    if args.auto_ocr:
        raise SystemExit('annotate --auto-ocr is disabled in the current schema: OCR should happen before write-in')
    print(json.dumps(annotate_photo(cfg, args), ensure_ascii=False, indent=2))


def cmd_remember(args):
    cfg = load_runtime_config(args.config)
    image_path = Path(args.image).expanduser().resolve()
    if args.auto_ocr and not args.ocr_text:
        args.ocr_text = maybe_run_ocr(image_path, cfg)
    if args.auto_analyze:
        analyzed = analyze_image(image_path, cfg, user_note=args.note, ocr_text=args.ocr_text)
        apply_analysis_to_args(args, analyzed)
    return plugin_success('remember', remember_prepared(args, cfg))


def cmd_remember_chat(args):
    cfg = load_runtime_config(args.config)
    analyzed = normalize_analysis(json.loads(args.analysis_json))
    apply_analysis_to_args(args, analyzed)
    return plugin_success('remember-chat', remember_prepared(args, cfg))


def cmd_embed(args):
    cfg = load_runtime_config(args.config)
    print(json.dumps(embed_photo(cfg, args.id, args.model), ensure_ascii=False, indent=2))


def cmd_inspect(args):
    cfg = load_runtime_config(args.config)
    return plugin_success('inspect', inspect_photo(cfg, args.image))


def cmd_doctor(args):
    payload = run_doctor(args.config, args.dropbox_config, args.safe_out_dir)
    if not payload['ok']:
        return {
            'ok': False,
            'command': 'doctor',
            **payload,
        }
    return {
        'command': 'doctor',
        **payload,
    }


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

    s = sub.add_parser('recall')
    s.add_argument('query')
    s.add_argument('--limit', type=int, default=10)
    s.add_argument('--semantic', action='store_true')
    s.add_argument('--model', default='')
    s.add_argument('--out', default='')
    s.set_defaults(func=cmd_recall)

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

    s = sub.add_parser('doctor')
    s.add_argument('--dropbox-config', default='')
    s.add_argument('--safe-out-dir', default='')
    s.set_defaults(func=cmd_doctor)

    return p


def cmd_search(args):
    cfg = load_runtime_config(args.config)
    result = run_search(cfg, args.query.strip(), semantic=args.semantic, model=args.model, limit=args.limit)
    return plugin_success('search', result)


def main():
    args = build_parser().parse_args()
    try:
        result = args.func(args)
    except SystemExit as e:
        if getattr(args, 'cmd', '') in PLUGIN_COMMANDS:
            message = str(e)
            if not message or message.isdigit():
                message = 'command failed'
            emit_plugin_error(args.cmd, message, int(e.code) if isinstance(e.code, int) else 1)
        raise
    except Exception as e:
        if getattr(args, 'cmd', '') in PLUGIN_COMMANDS:
            emit_plugin_error(args.cmd, str(e), 1)
        raise

    if result is None:
        return

    if getattr(args, 'cmd', '') == 'doctor':
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result['ok']:
            raise SystemExit(1)
        return

    if getattr(args, 'cmd', '') in PLUGIN_COMMANDS:
        print(json.dumps(result, ensure_ascii=False, indent=2))
