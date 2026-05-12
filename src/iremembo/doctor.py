import json
import os
import shutil
import tempfile
from pathlib import Path

DEFAULT_IREMEMBO_CONFIG = Path.home() / '.config' / 'iremembo' / 'config.json'
DEFAULT_DROPBOX_CONFIG = Path.home() / '.config' / 'iremembo' / 'dropbox.json'
DEFAULT_SAFE_SEND_DIR = Path.home() / '.openclaw' / 'workspace' / 'tmp' / 'iremembo-send'


def resolve_file_candidate(raw: str, env_name: str, default_path: Path) -> dict:
    source = 'default'
    chosen = raw
    if raw:
        source = 'arg'
    else:
        env_value = os.environ.get(env_name, '')
        if env_value:
            chosen = env_value
            source = 'env'
        else:
            chosen = str(default_path)
    path = Path(chosen).expanduser().resolve()
    return {
        'path': str(path),
        'source': source,
        'exists': path.exists(),
    }


def writable_directory_check(path: Path) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path, delete=True):
        pass
    return {
        'path': str(path),
        'ok': True,
    }


def validate_local_config(path: Path) -> tuple[dict, list[str]]:
    result = {
        'path': str(path),
        'exists': path.exists(),
        'valid_json': False,
        'missing_keys': [],
        'db_path': '',
        'thumb_dir': '',
        'dropbox_tool': '',
    }
    problems = []
    if not path.exists():
        problems.append(f'config file not found: {path}')
        return result, problems

    try:
        with path.open() as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        problems.append(f'config JSON is invalid: {e}')
        return result, problems

    result['valid_json'] = True
    required = ['db_path', 'thumb_dir', 'dropbox_base', 'dropbox_tool']
    result['missing_keys'] = [key for key in required if not cfg.get(key)]
    if result['missing_keys']:
        problems.append(f"config is missing required keys: {', '.join(result['missing_keys'])}")

    result['db_path'] = str(Path(cfg.get('db_path', '')).expanduser()) if cfg.get('db_path') else ''
    result['thumb_dir'] = str(Path(cfg.get('thumb_dir', '')).expanduser()) if cfg.get('thumb_dir') else ''
    result['dropbox_tool'] = str(Path(cfg.get('dropbox_tool', '')).expanduser()) if cfg.get('dropbox_tool') else ''
    return result, problems


def run_doctor(config_arg: str = '', dropbox_config_arg: str = '', safe_out_dir: str = '') -> dict:
    config_file = resolve_file_candidate(config_arg, 'IREMEMBO_CONFIG', DEFAULT_IREMEMBO_CONFIG)
    dropbox_file = resolve_file_candidate(dropbox_config_arg, 'DROPBOX_CONFIG', DEFAULT_DROPBOX_CONFIG)
    safe_send_dir = Path(safe_out_dir).expanduser().resolve() if safe_out_dir else DEFAULT_SAFE_SEND_DIR

    problems = []
    warnings = []
    payload = {
        'ok': False,
        'config_file': config_file,
        'dropbox_config_file': dropbox_file,
        'config': {},
        'paths': {},
        'dependencies': {},
        'problems': problems,
        'warnings': warnings,
    }

    config_result, config_problems = validate_local_config(Path(config_file['path']))
    payload['config'] = config_result
    problems.extend(config_problems)

    if not dropbox_file['exists']:
        problems.append(f"Dropbox config file not found: {dropbox_file['path']}")

    if config_result.get('db_path'):
        try:
            payload['paths']['db_parent'] = writable_directory_check(Path(config_result['db_path']).expanduser().resolve().parent)
        except OSError as e:
            problems.append(f'database parent directory is not writable: {e}')
    if config_result.get('thumb_dir'):
        try:
            payload['paths']['thumb_dir'] = writable_directory_check(Path(config_result['thumb_dir']).expanduser().resolve())
        except OSError as e:
            problems.append(f'thumb directory is not writable: {e}')
    try:
        payload['paths']['safe_send_dir'] = writable_directory_check(safe_send_dir)
    except OSError as e:
        problems.append(f'safe send directory is not writable: {e}')

    if config_result.get('dropbox_tool'):
        dropbox_tool_path = Path(config_result['dropbox_tool']).expanduser().resolve()
        payload['dependencies']['dropbox_tool'] = {
            'path': str(dropbox_tool_path),
            'exists': dropbox_tool_path.exists(),
        }
        if not dropbox_tool_path.exists():
            problems.append(f"dropbox_tool not found: {dropbox_tool_path}")

    ffmpeg = shutil.which('ffmpeg')
    payload['dependencies']['ffmpeg'] = {'found': bool(ffmpeg), 'path': ffmpeg or ''}
    if not ffmpeg:
        warnings.append('ffmpeg not found; thumbnail generation will fall back to copying the source file')

    tesseract = shutil.which('tesseract')
    payload['dependencies']['tesseract'] = {'found': bool(tesseract), 'path': tesseract or ''}
    if not tesseract:
        warnings.append('tesseract not found; OCR auto-detection will be unavailable unless analysis_command is configured')

    payload['ok'] = not problems
    return payload
