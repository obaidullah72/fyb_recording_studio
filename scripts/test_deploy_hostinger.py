"""Offline integration/regression tests for deployment and recovery.

Run: python3 -m unittest discover -s scripts -p 'test_deploy_hostinger.py' -v
"""
from contextlib import contextmanager
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import deploy_hostinger as driver
import hostinger_remote as remote


@contextmanager
def serve(directory, behavior='normal'):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *_):
            pass

        def do_GET(self):
            if behavior == 'loop':
                self.send_response(302)
                self.send_header('Location', self.path)
                self.end_headers()
            elif behavior == 'error':
                self.send_error(503)
            elif behavior == 'foreign':
                self.send_response(302)
                self.send_header('Location', 'https://example.invalid/')
                self.end_headers()
            elif behavior == 'fallback':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'Wrong website')
            else:
                super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix='hostinger-tests-')
        cls.fixture_root = Path(cls.fixture.name)
        cls.package = cls.fixture_root / 'package'
        driver.package(Path(__file__).resolve().parent.parent, cls.package)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hostinger-remote-')
        self.root = Path(self.temporary.name)
        self.home = self.root / 'account'
        self.destination = self.home / 'domains' / 'example.test' / 'public_html'
        self.destination.mkdir(parents=True)
        self.destination.joinpath('unrelated.txt').write_text('Keep me')
        self.destination.joinpath('.htaccess').write_text('# Preserve configuration')
        self.destination.joinpath('index.html').write_text('Original site')
        self.release = uuid.uuid4().hex
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start()
        self.old_umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self.old_umask)
        self.home_patch.stop()
        self.temporary.cleanup()

    def call(self, action, **kwargs):
        return remote.main({'action': action, 'destination': str(self.destination),
                            'release': self.release, **kwargs})

    def stage(self):
        result = self.call('begin')
        archive = Path(result['archive'])
        shutil.copyfile(self.package / 'payload.tar', archive)
        return archive

    def activate(self):
        archive = self.stage()
        self.call('activate', archive_sha256=remote.digest(archive))
        return archive

    def assert_preserved(self):
        self.assertEqual((self.destination / 'unrelated.txt').read_text(), 'Keep me')
        self.assertEqual((self.destination / '.htaccess').read_text(), '# Preserve configuration')

    def test_public_allowlist_and_exact_commit(self):
        public = self.package / 'public'
        files = json.loads((public / 'deployment.json').read_text())['files']
        self.assertEqual(len(files), 42)
        self.assertTrue(driver.PUBLIC_FILES <= set(files))
        self.assertFalse(any(name.startswith(('scripts/', '.git')) or name.endswith('.md') for name in files))
        repo = Path(__file__).resolve().parent.parent
        original = subprocess.check_output(['git', '-C', str(repo), 'show', 'HEAD:index.html'])
        self.assertEqual((public / 'index.html').read_bytes(), original)

    def test_nested_repository_source(self):
        repo = self.root / 'nested-repo'
        site = repo / 'frontend'
        shutil.copytree(self.package / 'public', site)
        (site / 'deployment.json').unlink()
        for args in (['init', '-q'], ['add', '.'],
                     ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture']):
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DEPLOY_COMMIT', None)
            driver.package(site, self.root / 'nested-package')
        self.assertTrue((self.root / 'nested-package/public/index.html').is_file())
        self.assertFalse((self.root / 'nested-package/public/frontend').exists())

    def test_missing_asset_fails(self):
        public = self.root / 'public'
        shutil.copytree(self.package / 'public', public)
        (public / 'assets/images/logo.png').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing local reference'):
            driver.validate_site(public)

    def test_corrupt_asset_manifest_fails(self):
        public = self.root / 'public'
        shutil.copytree(self.package / 'public', public)
        (public / 'assets/images/logo.png').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'Asset does not match'):
            driver.validate_site(public)

    def test_success_and_manual_recovery_preserve_unrelated(self):
        archive = self.activate()
        self.call('finish')
        self.assertEqual(remote.digest(self.destination / 'index.html'), remote.digest(self.package / 'public/index.html'))
        self.assertEqual((self.destination / 'home').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.destination / 'index.html').stat().st_mode & 0o777, 0o644)
        self.assertTrue((archive.parent / 'backup/index.html').is_file())
        self.call('rollback')
        self.assertEqual((self.destination / 'index.html').read_text(), 'Original site')
        self.assertFalse((self.destination / 'app.js').exists())
        self.assert_preserved()

    def test_corrupt_transfer_leaves_live_files_untouched(self):
        archive = self.stage()
        archive.write_bytes(b'incomplete transfer')
        with self.assertRaisesRegex(ValueError, 'archive checksum'):
            self.call('activate', archive_sha256='0' * 64)
        self.call('rollback')
        self.assertEqual((self.destination / 'index.html').read_text(), 'Original site')
        self.assert_preserved()

    def test_lock_serializes_and_foreign_owner_cannot_recover(self):
        self.stage()
        self.release = uuid.uuid4().hex
        with self.assertRaises(FileExistsError):
            self.call('begin')
        with self.assertRaisesRegex(ValueError, 'lock'):
            self.call('rollback')

    def test_symlink_collision_fails_before_mutation(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.destination / 'assets').symlink_to(outside, target_is_directory=True)
        archive = self.stage()
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.call('activate', archive_sha256=remote.digest(archive))
        self.call('rollback')
        self.assertEqual((self.destination / 'index.html').read_text(), 'Original site')
        self.assertEqual(list(outside.iterdir()), [])

    def test_archive_traversal_rejected(self):
        archive = self.stage()
        with tarfile.open(archive, 'w') as tar:
            entry = tarfile.TarInfo('../escape')
            entry.size = 3
            tar.addfile(entry, io.BytesIO(b'bad'))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.call('activate', archive_sha256=remote.digest(archive))
        self.call('rollback')
        self.assertFalse((archive.parent / 'escape').exists())

    def test_partial_activation_restored(self):
        archive = self.stage()
        original = remote.install
        count = 0

        def interrupted(source, destination):
            nonlocal count
            count += 1
            if count == 6:
                raise OSError('simulated disk failure')
            original(source, destination)

        with patch.object(remote, 'install', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.call('activate', archive_sha256=remote.digest(archive))
        self.call('rollback')
        self.assertEqual((self.destination / 'index.html').read_text(), 'Original site')
        self.assertFalse((self.destination / 'app.js').exists())
        self.assert_preserved()

    def test_stale_managed_files_only_and_rollback(self):
        self.activate()
        self.call('finish')
        first_release = self.release
        self.release = uuid.uuid4().hex
        archive = self.stage()
        # Model a previous release which also managed one now-removed file.
        state = archive.parent.parent
        current = json.loads((state / 'current.json').read_text())
        (self.destination / 'old.html').write_text('Old managed page')
        current['files']['old.html'] = remote.digest(self.destination / 'old.html')
        remote.write_json(state / 'current.json', current)
        self.call('activate', archive_sha256=remote.digest(archive))
        self.assertFalse((self.destination / 'old.html').exists())
        self.call('finish')
        second_release = self.release
        self.release = first_release
        with self.assertRaisesRegex(ValueError, 'latest'):
            self.call('rollback')
        self.release = second_release
        self.call('rollback')
        self.assertEqual((self.destination / 'old.html').read_text(), 'Old managed page')
        self.assert_preserved()

    def test_http_checks_all_routes_and_assets(self):
        with serve(self.package / 'public') as base:
            driver.health(self.package / 'public', base)

    def test_http_failures_redirects_and_wrong_site(self):
        for behavior in ('loop', 'error', 'foreign', 'fallback'):
            with self.subTest(behavior=behavior), serve(self.package / 'public', behavior) as base:
                with self.assertRaisesRegex(RuntimeError, 'HTTP verification failed'):
                    driver.check_url(base, '', remote.digest(self.package / 'public/index.html'), uuid.uuid4().hex)

    def test_missing_secrets_report_names_only(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Missing required secrets: HOSTINGER_DEPLOY_PATH'):
                driver.configuration()

    def test_driver_transfer_and_health_failures_restore_and_fail(self):
        destination = self.destination

        class LocalRemote:
            def __init__(self, values, temporary, release):
                self.release = release

            def call(self, action, **extra):
                return remote.main({'action': action, 'destination': str(destination),
                                    'release': self.release, **extra})

            def upload(self, archive, target, rsync):
                shutil.copyfile(archive, target)

        for phase in ('upload', 'health'):
            with self.subTest(phase=phase), serve(destination) as base:
                values = {'SITE_URL': base}
                failure = RuntimeError('simulated ' + phase + ' failure')
                target = LocalRemote if phase == 'upload' else driver
                attribute = 'upload' if phase == 'upload' else 'health'
                with patch.object(driver, 'configuration', return_value=values), \
                        patch.object(driver, 'Remote', LocalRemote), \
                        patch.object(target, attribute, side_effect=failure):
                    with self.assertRaisesRegex(RuntimeError, 'simulated'):
                        driver.deploy(self.package)
                self.assertEqual((destination / 'index.html').read_text(), 'Original site')
                self.assertFalse((destination / 'app.js').exists())
                self.assert_preserved()

    def test_remote_command_quoting_and_transport_options(self):
        values = {name: 'placeholder' for name in driver.REQUIRED_SECRETS}
        values.update(HOSTINGER_SSH_HOST='example.invalid', HOSTINGER_SSH_PORT='65002',
                      HOSTINGER_SSH_USER='account', HOSTINGER_DEPLOY_PATH="/home/account/site's directory")
        with patch.object(driver, 'command', return_value=b'{}') as run:
            client = driver.Remote(values, self.root, self.release)
            client.call('begin')
            args = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('65002', args)
            self.assertEqual(driver.shlex.split(args[-1]), ['python3', '-c', client.program])
            request = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(request['destination'], values['HOSTINGER_DEPLOY_PATH'])
            with patch.object(driver.shutil, 'which', return_value='/usr/bin/rsync'):
                client.upload(self.package / 'payload.tar', "/private/site's directory/payload.tar", True)
            self.assertIn('--protect-args', run.call_args.args[0])
            self.assertNotIn('--delete', run.call_args.args[0])
            client.upload(self.package / 'payload.tar', '/private/payload.tar', False)
            self.assertEqual(run.call_args.args[0][0], 'scp')
            self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])

    def test_broad_destination_rejected(self):
        self.destination = self.home
        with self.assertRaisesRegex(ValueError, 'Refusing'):
            self.call('begin')


if __name__ == '__main__':
    unittest.main()
