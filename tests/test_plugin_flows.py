import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from iremembo.analysis import normalize_analysis
from iremembo.storage import run_search


class PluginFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.remote_root = self.tmp / 'remote'
        self.remote_root.mkdir()
        self.thumb_dir = self.tmp / 'thumbs'
        self.db_path = self.tmp / 'photo-memory.db'
        self.image_path = self.tmp / 'sample.jpg'
        self.image_path.write_bytes(b'not-a-real-jpeg-but-good-enough-for-tests')
        self.dropbox_tool_path = self.tmp / 'fake_dropbox_tool.py'
        self.dropbox_tool_path.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json
            import shutil
            import sys
            from pathlib import Path

            ROOT = Path({str(self.remote_root)!r})


            def remote_path(raw: str) -> Path:
                return ROOT / raw.lstrip('/')


            def main():
                cmd = sys.argv[1]
                if cmd == 'upload':
                    local = Path(sys.argv[2])
                    remote = remote_path(sys.argv[3])
                    remote.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local, remote)
                    print(json.dumps({{'path': sys.argv[3]}}))
                    return
                if cmd == 'stat':
                    remote = remote_path(sys.argv[2])
                    if not remote.exists():
                        raise SystemExit(1)
                    print(json.dumps({{'path': sys.argv[2]}}))
                    return
                if cmd == 'download':
                    remote = remote_path(sys.argv[2])
                    local = Path(sys.argv[3])
                    local.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(remote, local)
                    print(str(local))
                    return
                if cmd == 'delete':
                    remote = remote_path(sys.argv[2])
                    if remote.exists():
                        remote.unlink()
                    print(json.dumps({{'path': sys.argv[2]}}))
                    return
                raise SystemExit(f'unsupported fake dropbox command: {{cmd}}')


            if __name__ == '__main__':
                main()
            """))
        self.dropbox_tool_path.chmod(0o755)

        self.config_path = self.tmp / 'config.json'
        self.config_path.write_text(json.dumps({
            'db_path': str(self.db_path),
            'thumb_dir': str(self.thumb_dir),
            'dropbox_base': '/photo-memory',
            'dropbox_tool': str(self.dropbox_tool_path),
            'embedding_model': 'text-embedding-3-small',
            'analysis_command': [],
        }))
        self.dropbox_config_path = self.tmp / 'dropbox.json'
        self.dropbox_config_path.write_text('{}')

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *args, env=None):
        cmd = [sys.executable, str(REPO_ROOT / 'src' / 'photo_memory.py'), '--config', str(self.config_path), *args]
        return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env)

    def run_wrapper(self, *args, env=None):
        cmd = [sys.executable, str(REPO_ROOT / 'scripts' / 'remember_to_iremembo.py'), *args]
        return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env)

    def wrapper_env(self):
        env = os.environ.copy()
        env['IREMEMBO_CONFIG'] = str(self.config_path)
        env['DROPBOX_CONFIG'] = str(self.dropbox_config_path)
        return env

    def test_normalize_analysis_trims_and_defaults(self):
        normalized = normalize_analysis({
            'summary': '  remembered photo  ',
            'tags': [' alpha ', '', 'beta'],
            'entities': {
                'objects': [' cat ', '', 'toy'],
                'people': [' Alice '],
            },
            'ocr_text': '  visible text  ',
        })
        self.assertEqual(normalized, {
            'summary': 'remembered photo',
            'tags': ['alpha', 'beta'],
            'entities': {
                'dates': [],
                'times': [],
                'people': ['Alice'],
                'places': [],
                'organizations': [],
                'objects': ['cat', 'toy'],
            },
            'ocr_text': 'visible text',
        })

    def test_wrapper_reports_missing_config_as_json(self):
        result = self.run_wrapper(str(self.image_path), env={})
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['command'], 'remember-to-iremembo')
        self.assertIn('IREMEMBO_CONFIG is required', payload['error'])

    def test_wrapper_rejects_invalid_analysis_json(self):
        result = self.run_wrapper(str(self.image_path), '--analysis-json', '{', env=self.wrapper_env())
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['command'], 'remember-to-iremembo')
        self.assertIn('invalid --analysis-json', payload['error'])

    def test_remember_chat_persists_record_and_dropbox_copy(self):
        result = self.run_cli(
            'remember-chat',
            str(self.image_path),
            '--analysis-json',
            json.dumps({'summary': 'Family photo', 'tags': ['family'], 'entities': {'objects': ['photo']}, 'ocr_text': ''}),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['command'], 'remember-chat')
        self.assertEqual(payload['status'], 'uploaded')
        self.assertFalse(payload['dedup'])

        remote_copy = self.remote_root / payload['dropbox_path'].lstrip('/')
        self.assertTrue(remote_copy.exists())

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT summary, status, dropbox_path FROM photos WHERE id = ?', (payload['id'],)).fetchone()
        self.assertEqual(row, ('Family photo', 'uploaded', payload['dropbox_path']))

    def test_second_remember_returns_existing_record(self):
        first = self.run_cli(
            'remember-chat',
            str(self.image_path),
            '--analysis-json',
            json.dumps({'summary': 'Original summary', 'tags': [], 'entities': {'objects': ['photo']}, 'ocr_text': ''}),
        )
        second = self.run_cli(
            'remember-chat',
            str(self.image_path),
            '--analysis-json',
            json.dumps({'summary': 'Updated summary', 'tags': [], 'entities': {'objects': ['photo']}, 'ocr_text': ''}),
        )
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        first_payload = json.loads(first.stdout)
        second_payload = json.loads(second.stdout)
        self.assertTrue(first_payload['ok'])
        self.assertTrue(second_payload['ok'])
        self.assertEqual(second_payload['id'], first_payload['id'])
        self.assertTrue(second_payload['dedup'])
        self.assertEqual(second_payload['status'], 'uploaded')

    def test_search_and_recall_return_plugin_facing_json_shapes(self):
        remember = self.run_cli(
            'remember-chat',
            str(self.image_path),
            '--analysis-json',
            json.dumps({'summary': 'Brandon Sanderson book photo', 'tags': ['book'], 'entities': {'objects': ['book']}, 'ocr_text': ''}),
        )
        self.assertEqual(remember.returncode, 0, msg=remember.stderr)

        search = self.run_cli('search', 'Brandon')
        self.assertEqual(search.returncode, 0, msg=search.stderr)
        search_payload = json.loads(search.stdout)
        self.assertTrue(search_payload['ok'])
        self.assertEqual(search_payload['command'], 'search')
        self.assertEqual(search_payload['query'], 'Brandon')
        self.assertFalse(search_payload['semantic'])
        self.assertTrue(search_payload['results'])
        self.assertIn('score', search_payload['results'][0])

        out_path = self.tmp / 'send' / 'result.jpg'
        recall = self.run_cli('recall', 'Brandon', '--out', str(out_path))
        self.assertEqual(recall.returncode, 0, msg=recall.stderr)
        recall_payload = json.loads(recall.stdout)
        self.assertTrue(recall_payload['ok'])
        self.assertEqual(recall_payload['command'], 'recall')
        self.assertEqual(recall_payload['query'], 'Brandon')
        self.assertIn('match', recall_payload)
        self.assertIn('fetched', recall_payload)
        self.assertEqual(recall_payload['fetched']['local_path'], str(out_path.resolve()))
        self.assertTrue(out_path.exists())

    def test_auto_embed_failure_rolls_back_partial_remember_state(self):
        env = os.environ.copy()
        env.pop('OPENAI_API_KEY', None)

        result = self.run_cli(
            'remember-chat',
            str(self.image_path),
            '--analysis-json',
            json.dumps({'summary': 'Needs embedding', 'tags': ['photo'], 'entities': {'objects': ['photo']}, 'ocr_text': ''}),
            '--auto-embed',
            env=env,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['command'], 'remember-chat')
        self.assertIn('OPENAI_API_KEY is required for embeddings', payload['error'])

        with sqlite3.connect(self.db_path) as conn:
            photo_count = conn.execute('SELECT COUNT(*) FROM photos').fetchone()[0]
            embedding_count = conn.execute('SELECT COUNT(*) FROM photo_embeddings').fetchone()[0]
        self.assertEqual(photo_count, 0)
        self.assertEqual(embedding_count, 0)
        self.assertEqual([path for path in self.remote_root.rglob('*') if path.is_file()], [])

    def test_non_semantic_search_avoids_vector_payloads_and_limits_candidates(self):
        remember = self.run_cli(
            'remember-chat',
            str(self.image_path),
            '--analysis-json',
            json.dumps({'summary': 'Brandon Sanderson book photo', 'tags': ['book'], 'entities': {'objects': ['book']}, 'ocr_text': ''}),
        )
        self.assertEqual(remember.returncode, 0, msg=remember.stderr)

        executed_sql = []
        real_connect = sqlite3.connect

        class ConnectionProxy:
            def __init__(self, conn):
                super().__setattr__('_conn', conn)

            def __enter__(self):
                self._conn.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._conn.__exit__(exc_type, exc, tb)

            def execute(self, sql, params=()):
                executed_sql.append(sql)
                return self._conn.execute(sql, params)

            def __setattr__(self, name, value):
                if name == '_conn':
                    super().__setattr__(name, value)
                    return
                setattr(self._conn, name, value)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        def connect_spy(*args, **kwargs):
            return ConnectionProxy(real_connect(*args, **kwargs))

        with mock.patch('iremembo.storage.sqlite3.connect', side_effect=connect_spy):
            payload = run_search({'db_path': str(self.db_path), 'embedding_model': 'text-embedding-3-small'}, 'Brandon', semantic=False, limit=3)

        self.assertTrue(payload['results'])
        select_sql = next(sql for sql in executed_sql if 'FROM photos p' in sql)
        self.assertIn('LIMIT ?', select_sql)
        self.assertNotIn('e.vector_json', select_sql)

    def test_doctor_reports_ready_state_for_local_plugin_setup(self):
        safe_out_dir = self.tmp / 'safe-send'
        result = self.run_cli(
            'doctor',
            '--dropbox-config',
            str(self.dropbox_config_path),
            '--safe-out-dir',
            str(safe_out_dir),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['command'], 'doctor')
        self.assertTrue(payload['config_file']['exists'])
        self.assertTrue(payload['dropbox_config_file']['exists'])
        self.assertTrue(payload['paths']['safe_send_dir']['ok'])

    def test_cli_errors_for_plugin_commands_are_json(self):
        result = self.run_cli('remember-chat', str(self.image_path), '--analysis-json', '{')
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['command'], 'remember-chat')
        self.assertIn('Expecting property name enclosed in double quotes', payload['error'])


if __name__ == '__main__':
    unittest.main()
