#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://api.dropboxapi.com/2'
CONTENT = 'https://content.dropboxapi.com/2'
TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'


def get_config_path() -> Path:
    raw = os.environ.get('DROPBOX_CONFIG')
    if not raw:
        raise SystemExit('DROPBOX_CONFIG is required and must point to a local-only Dropbox secret JSON file')
    return Path(raw).expanduser().resolve()


def load_cfg():
    config_path = get_config_path()
    with config_path.open() as f:
        return json.load(f)


def save_cfg(cfg):
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(cfg, f, indent=2)
        f.write('\n')
    os.chmod(tmp, 0o600)
    tmp.replace(config_path)
    os.chmod(config_path, 0o600)


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def refresh_access_token(cfg):
    if not cfg.get('refresh_token'):
        return cfg
    resp = post_form(TOKEN_URL, {
        'grant_type': 'refresh_token',
        'refresh_token': cfg['refresh_token'],
        'client_id': cfg['app_key'],
        'client_secret': cfg['app_secret'],
    })
    cfg['access_token'] = resp['access_token']
    if 'expires_in' in resp:
        cfg['expires_in'] = resp['expires_in']
    if 'scope' in resp:
        cfg['scope'] = resp['scope']
    save_cfg(cfg)
    return cfg


def api_json(endpoint, cfg, payload=None):
    body = json.dumps(payload if payload is not None else None).encode()
    req = urllib.request.Request(
        API + endpoint,
        data=body,
        headers={
            'Authorization': f"Bearer {cfg['access_token']}",
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        if e.code == 401 and 'expired_access_token' in msg and cfg.get('refresh_token'):
            cfg = refresh_access_token(cfg)
            return api_json(endpoint, cfg, payload)
        raise RuntimeError(f'{e.code} {msg}')


def api_content_download(endpoint, cfg, arg_obj):
    req = urllib.request.Request(
        CONTENT + endpoint,
        data=b'',
        headers={
            'Authorization': f"Bearer {cfg['access_token']}",
            'Dropbox-API-Arg': json.dumps(arg_obj),
            'Content-Type': 'text/plain; charset=utf-8',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        if e.code == 401 and 'expired_access_token' in msg and cfg.get('refresh_token'):
            cfg = refresh_access_token(cfg)
            return api_content_download(endpoint, cfg, arg_obj)
        raise RuntimeError(f'{e.code} {msg}')


def api_content_upload(endpoint, cfg, arg_obj, data):
    req = urllib.request.Request(
        CONTENT + endpoint,
        data=data,
        headers={
            'Authorization': f"Bearer {cfg['access_token']}",
            'Dropbox-API-Arg': json.dumps(arg_obj),
            'Content-Type': 'application/octet-stream',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        if e.code == 401 and 'expired_access_token' in msg and cfg.get('refresh_token'):
            cfg = refresh_access_token(cfg)
            return api_content_upload(endpoint, cfg, arg_obj, data)
        raise RuntimeError(f'{e.code} {msg}')


def cmd_whoami(args):
    cfg = load_cfg()
    print(json.dumps(api_json('/users/get_current_account', cfg), ensure_ascii=False, indent=2))


def cmd_list(args):
    cfg = load_cfg()
    path = args.path
    resp = api_json('/files/list_folder', cfg, {'path': path})
    while True:
        for e in resp.get('entries', []):
            tag = e.get('.tag', '?')
            print(f"{tag}\t{e.get('path_display', e.get('name', ''))}")
        if not resp.get('has_more'):
            break
        resp = api_json('/files/list_folder/continue', cfg, {'cursor': resp['cursor']})


def cmd_upload(args):
    cfg = load_cfg()
    local = Path(args.local)
    data = local.read_bytes()
    resp = api_content_upload('/files/upload', cfg, {
        'path': args.remote,
        'mode': 'overwrite' if args.overwrite else 'add',
        'autorename': not args.overwrite,
        'mute': False,
        'strict_conflict': False,
    }, data)
    print(json.dumps(resp, ensure_ascii=False, indent=2))


def cmd_download(args):
    cfg = load_cfg()
    data, headers = api_content_download('/files/download', cfg, {'path': args.remote})
    Path(args.local).write_bytes(data)
    print(args.local)


def cmd_share(args):
    cfg = load_cfg()
    try:
        resp = api_json('/sharing/create_shared_link_with_settings', cfg, {'path': args.path})
    except RuntimeError as e:
        msg = str(e)
        if 'shared_link_already_exists' in msg:
            resp = api_json('/sharing/list_shared_links', cfg, {'path': args.path, 'direct_only': True})
            links = resp.get('links', [])
            if not links:
                raise
            print(links[0]['url'])
            return
        raise
    print(resp['url'])


def main():
    p = argparse.ArgumentParser(description='Simple Dropbox helper for local automation')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('whoami')
    s.set_defaults(func=cmd_whoami)

    s = sub.add_parser('list')
    s.add_argument('path', nargs='?', default='')
    s.set_defaults(func=cmd_list)

    s = sub.add_parser('upload')
    s.add_argument('local')
    s.add_argument('remote')
    s.add_argument('--overwrite', action='store_true')
    s.set_defaults(func=cmd_upload)

    s = sub.add_parser('download')
    s.add_argument('remote')
    s.add_argument('local')
    s.set_defaults(func=cmd_download)

    s = sub.add_parser('share')
    s.add_argument('path')
    s.set_defaults(func=cmd_share)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
