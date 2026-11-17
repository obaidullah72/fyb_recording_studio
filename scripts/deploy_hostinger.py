#!/usr/bin/env python3
"""Validate, publish, verify, and recover the static site without dependencies."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import uuid

from hostinger_remote import digest, safe_name

# Explicit public surface: scripts/ contains QA/deployment tooling, not runtime JS.
PUBLIC_FILES = {'index.html', '404.html', 'app.js', 'styles.css',
                'asset-manifest.json', 'verification.json'}
PUBLIC_DIRS = {'assets', 'home', 'hello-world', 'category'}
PUBLIC_EXTENSIONS = {'.html', '.js', '.css', '.json', '.png', '.jpg', '.jpeg',
                     '.svg', '.webp', '.gif', '.ico', '.woff', '.woff2', '.ttf',
                     '.mp4', '.webm', '.avif'}
REQUIRED_SECRETS = ('HOSTINGER_DEPLOY_PATH', 'HOSTINGER_KNOWN_HOSTS',
                    'HOSTINGER_SSH_HOST', 'HOSTINGER_SSH_PORT',
                    'HOSTINGER_SSH_PRIVATE_KEY', 'HOSTINGER_SSH_USER', 'SITE_URL')


def command(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise RuntimeError('Command failed: ' + Path(args[0]).name + ' (details suppressed to protect configuration)')
    return result.stdout


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.urls = []
        self.inline_css = []
        self.in_style = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'base':
            self.base = attrs.get('href')
        else:
            self.urls.extend(attrs[key] for key in ('src', 'href', 'poster') if attrs.get(key))
        if attrs.get('srcset'):
            self.urls.extend(part.strip().split()[0] for part in attrs['srcset'].split(',') if part.strip())
        if attrs.get('style'):
            self.inline_css.append(attrs['style'])
        self.in_style = tag == 'style' or self.in_style

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.inline_css.append(data)


def css_urls(text):
    return [match[1] for match in re.findall(r'url\(\s*([\'\"]?)(.*?)\1\s*\)', text)] + re.findall(r'@import\s+[\'\"]([^\'\"]+)', text)


def validate_site(root):
    if not (root / 'index.html').is_file():
        raise ValueError('Publish directory has no index.html')
    origin = 'https://static.invalid/'
    count = 0
    for file in sorted(root.rglob('*')):
        if not file.is_file() or file.suffix not in {'.html', '.css'}:
            continue
        base = urljoin(origin, file.relative_to(root).as_posix())
        if file.suffix == '.html':
            parser = References()
            parser.feed(file.read_text())
            if parser.base:
                base = urljoin(base, parser.base)
                if urlsplit(base).netloc != 'static.invalid':
                    raise ValueError('External HTML base is not supported')
            refs = parser.urls + css_urls('\n'.join(parser.inline_css))
        else:
            refs = css_urls(file.read_text())
        for reference in refs:
            url = urlsplit(urljoin(base, reference))
            if url.netloc != 'static.invalid' or url.scheme not in ('http', 'https'):
                continue
            name = unquote(url.path).lstrip('/')
            target = (root / name).resolve()
            if root.resolve() not in target.parents and target != root.resolve():
                raise ValueError('Reference escapes the publish directory')
            if target.is_dir():
                target /= 'index.html'
            if not target.is_file():
                raise ValueError(f'Missing local reference in {file.relative_to(root)}: {name}')
            count += 1
    for entry in json.loads((root / 'asset-manifest.json').read_text()):
        target = root / safe_name(entry['file'])
        if target.stat().st_size != entry['bytes'] or digest(target) != entry['sha256']:
            raise ValueError('Asset does not match asset-manifest.json: ' + entry['file'])
    json.loads((root / 'verification.json').read_text())
    return count


def package(source, output):
    source = source.resolve()
    repo = Path(command(['git', '-C', str(source), 'rev-parse', '--show-toplevel']).decode().strip())
    commit = os.environ.get('DEPLOY_COMMIT') or command(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('DEPLOY_COMMIT must be a full commit SHA')
    head = command(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    if head != commit:
        raise ValueError('Checkout does not match deployment commit')
    prefix = source.relative_to(repo).as_posix()
    prefix = '' if prefix == '.' else prefix + '/'
    if output.exists():
        raise ValueError('Package output must be a new directory')
    output.mkdir(parents=True)
    public = output / 'public'
    public.mkdir()
    listing = command(['git', '-C', str(repo), 'ls-tree', '-rz', commit]).split(b'\0')
    for line in filter(None, listing):
        metadata, name = line.split(b'\t', 1)
        name = name.decode()
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix):]
        parts = Path(relative).parts
        if relative not in PUBLIC_FILES and (not parts or parts[0] not in PUBLIC_DIRS):
            continue
        if (any(part.startswith('.') for part in parts)
                or Path(relative).suffix.lower() not in PUBLIC_EXTENSIONS
                or re.search(r'(?i)(^|[/_.-])(backup|bak|secret|credentials?|private|env)([/_.-]|$)', relative)):
            continue
        safe_name(relative)
        mode, kind, blob = metadata.decode().split()
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Public symlinks and submodules are not supported')
        target = public / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Read committed blobs, never uncommitted worktree data.
        target.write_bytes(command(['git', '-C', str(repo), 'cat-file', 'blob', blob]))
    for required in PUBLIC_FILES:
        if not (public / required).is_file():
            raise ValueError('Missing required public file: ' + required)
    references = validate_site(public)
    files = {path.relative_to(public).as_posix(): digest(path)
             for path in sorted(public.rglob('*')) if path.is_file()}
    manifest = {'commit': commit, 'files': files}
    (public / 'deployment.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
    with tarfile.open(output / 'payload.tar', 'w') as bundle:
        for name in sorted(files) + ['deployment.json']:
            bundle.add(public / name, arcname=name, recursive=False)
    print(f'Validated {len(files)} public files and {references} local references at commit {commit}.')


class LimitedRedirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # SITE_URL must be the canonical origin; never follow a login/foreign host.
        before, after = urlsplit(req.full_url), urlsplit(newurl)
        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
            raise ValueError('Health check redirected outside the configured origin')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(base, route, expected, token):
    url = base.rstrip('/') + '/' + route + '?deployment_check=' + token
    request = Request(url, headers={'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity',
                                    'User-Agent': 'Hostinger-Deployment-Check/1.0'})
    try:
        with build_opener(LimitedRedirects()).open(request, timeout=20) as response:
            if response.status != 200:
                raise ValueError('Unexpected HTTP status')
            h = hashlib.sha256()
            deadline = time.monotonic() + 120
            for block in iter(lambda: response.read(1024 * 1024), b''):
                if time.monotonic() > deadline:
                    raise TimeoutError('Health check exceeded time limit')
                h.update(block)
            if h.hexdigest() != expected:
                raise ValueError('HTTP content differs from the validated release')
    except Exception as error:
        raise RuntimeError('HTTP verification failed for ' + (route or '/') + ' (HTTP error, redirect, timeout, or content mismatch)') from error


def health(public, base):
    manifest = json.loads((public / 'deployment.json').read_text())
    token = uuid.uuid4().hex
    # Check every public asset, including video, and each existing directory route.
    for name, expected in {**manifest['files'], 'deployment.json': digest(public / 'deployment.json')}.items():
        route = name[:-10] if name.endswith('index.html') else name
        check_url(base, route, expected, token)
    print(f'HTTP checks passed for {len(manifest["files"]) + 1} routes and assets, including the commit record.')


def configuration():
    values = {name: os.environ.get(name, '') for name in REQUIRED_SECRETS}
    missing = [name for name, value in values.items() if not value.strip()]
    if missing:
        raise ValueError('Missing required secrets: ' + ', '.join(missing))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', values['HOSTINGER_SSH_HOST']):
        raise ValueError('HOSTINGER_SSH_HOST must be a hostname or IPv4 address')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*', values['HOSTINGER_SSH_USER']):
        raise ValueError('Invalid HOSTINGER_SSH_USER')
    port = values['HOSTINGER_SSH_PORT']
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid HOSTINGER_SSH_PORT')
    destination = values['HOSTINGER_DEPLOY_PATH']
    if not destination.startswith('/') or any(ord(c) < 32 for c in destination):
        raise ValueError('HOSTINGER_DEPLOY_PATH must be an absolute path without control characters')
    url = urlsplit(values['SITE_URL'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('SITE_URL must be a canonical HTTPS website URL without credentials, query, or fragment')
    return values


class Remote:
    def __init__(self, values, temporary, release):
        self.values, self.release = values, release
        key, hosts = temporary / 'key', temporary / 'known_hosts'
        key.write_text(values['HOSTINGER_SSH_PRIVATE_KEY'].replace('\r\n', '\n').rstrip() + '\n')
        hosts.write_text(values['HOSTINGER_KNOWN_HOSTS'].replace('\r\n', '\n').rstrip() + '\n')
        key.chmod(0o600)
        hosts.chmod(0o600)
        command(['ssh-keygen', '-y', '-P', '', '-f', str(key)], timeout=10)
        self.options = ['-F', '/dev/null', '-i', str(key), '-o', 'BatchMode=yes',
                        '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                        '-o', 'UserKnownHostsFile=' + str(hosts), '-o', 'GlobalKnownHostsFile=/dev/null',
                        '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=15',
                        '-o', 'ServerAliveCountMax=3', '-o', 'LogLevel=ERROR']
        self.target = values['HOSTINGER_SSH_USER'] + '@' + values['HOSTINGER_SSH_HOST']
        self.ssh = ['ssh', *self.options, '-p', values['HOSTINGER_SSH_PORT']]
        self.program = Path(__file__).with_name('hostinger_remote.py').read_text()

    def call(self, action, **extra):
        request = {'action': action, 'destination': self.values['HOSTINGER_DEPLOY_PATH'],
                   'release': self.release, **extra}
        output = command([*self.ssh, self.target, 'python3 -c ' + shlex.quote(self.program)],
                         input=json.dumps(request).encode(), timeout=900)
        return json.loads(output)

    def upload(self, archive, destination, rsync):
        if rsync and shutil.which('rsync'):
            command(['rsync', '--protect-args', '--checksum', '--times', '--chmod=F600',
                     '-e', shlex.join(self.ssh), '--', str(archive), self.target + ':' + destination], timeout=900)
            print('Archive transferred with rsync over strict SSH.')
        else:
            # Modern OpenSSH scp uses SFTP; no remote shell path interpolation.
            command(['scp', *self.options, '-P', self.values['HOSTINGER_SSH_PORT'],
                     str(archive), self.target + ':' + destination], timeout=900)
            print('rsync unavailable; archive transferred with OpenSSH SFTP/scp.')


def deploy(output, recover=None):
    values = configuration()
    release = recover or uuid.uuid4().hex
    if not re.fullmatch(r'[0-9a-f]{32}', release):
        raise ValueError('Invalid recovery release ID')
    print('Recovery release ID: ' + release, flush=True)
    with tempfile.TemporaryDirectory(prefix='hostinger-ssh-') as temporary:
        remote = Remote(values, Path(temporary), release)
        if recover:
            remote.call('rollback')
            print('Previous managed files restored and checksums verified. Verify the public site before resuming deployments.')
            return
        public = output / 'public'
        validate_site(public)
        manifest = json.loads((public / 'deployment.json').read_text())
        archive = output / 'payload.tar'
        began = False
        try:
            details = remote.call('begin')
            began = True
            check_url(values['SITE_URL'], 'hostinger-probe-' + release + '.txt',
                      hashlib.sha256(release.encode()).hexdigest(), release)
            print('Destination confirmed through SITE_URL.')
            remote.upload(archive, details['archive'], details['rsync'])
            remote.call('activate', archive_sha256=digest(archive))
            health(public, values['SITE_URL'])
            remote.call('finish')
        except BaseException:
            if began:
                try:
                    remote.call('rollback')
                    print('Deployment failed; previous managed files restored and checksums verified.', file=sys.stderr)
                except Exception:
                    print('Automatic recovery failed. The server lock and backup are retained; use the recovery release ID.', file=sys.stderr)
            raise
    print('Deployed and verified commit ' + manifest['commit'])
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'Deployed and HTTP-verified commit `{manifest["commit"]}`. Recovery release: `{release}`.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('package', 'deploy', 'health', 'recover'))
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, default=Path('/tmp/hostinger-package'))
    parser.add_argument('--release')
    args = parser.parse_args()
    if args.action == 'package':
        package(args.source, args.output)
    elif args.action == 'health':
        health(args.output / 'public', os.environ['SITE_URL'])
    else:
        if args.action == 'recover' and not args.release:
            parser.error('recover requires --release')
        deploy(args.output, args.release if args.action == 'recover' else None)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit('Deployment interrupted'))
    try:
        main()
    except Exception as error:
        # Exceptions may embed secret URLs, paths, SSH output, or key material.
        # Only deliberately sanitized validation messages are exposed.
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error), file=sys.stderr)
        else:
            print('Deployment operation failed (' + type(error).__name__ + '); configuration values suppressed.', file=sys.stderr)
        sys.exit(1)
