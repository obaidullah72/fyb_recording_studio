"""Private SSH-side transaction helper. Python 3 standard library only.

Executed from the checked-out commit via SSH; never published in the website.
Do not run directly against a production directory; use the deployment driver.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith('/') or '\\' in name
            or any(p in ('.', '..') or p.startswith('.') for p in parts)
            or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)):
        raise ValueError('Unsafe managed file name')
    return name


def safe_file(root, name):
    safe_name(name)
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink in managed destination')
    if current.exists() and not current.is_file():
        raise ValueError('Managed file conflicts with a directory')
    return current


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True))
    os.replace(temporary, path)


def install(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    # Newly created public directories must be traversable despite private umask.
    parent = destination.parent
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.hostinger-', dir=parent)
        os.close(fd)
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(request):
    os.umask(0o077)
    supplied = Path(request['destination'])
    if not supplied.is_absolute() or '..' in supplied.parts or not supplied.is_dir():
        raise ValueError('Destination must be an existing absolute website directory')
    destination = supplied.resolve()
    home = Path.home().resolve()
    if len(destination.parts) < 4 or destination == home or destination in home.parents:
        raise ValueError('Refusing a root, account home, or broad parent directory')
    # State and backups stay outside the public document root.
    state_base = home / '.hostinger-deploy'
    if state_base.is_symlink() or state_base == destination or destination in state_base.parents:
        raise ValueError('Private state must be outside the website')
    key = hashlib.sha256(str(destination).encode()).hexdigest()[:20]
    state = state_base / key
    if state.is_symlink():
        raise ValueError('Invalid state directory')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_base, 0o700)
    os.chmod(state, 0o700)
    release_id = request['release']
    if not re.fullmatch(r'[0-9a-f]{32}', release_id):
        raise ValueError('Invalid release ID')
    release = state / release_id
    lock = state / 'lock'
    action = request['action']
    probe = destination / ('hostinger-probe-' + release_id + '.txt')

    if action == 'begin':
        # A crash leaves the lock and snapshot in place for deliberate recovery.
        lock.mkdir(mode=0o700)
        (lock / 'owner').write_text(release_id)
        release.mkdir(mode=0o700)
        with probe.open('x') as stream:
            stream.write(release_id)
        probe.chmod(0o644)
        return {'archive': str(release / 'payload.tar'),
                'rsync': shutil.which('rsync') is not None}

    # Explicit recovery of the most recent successful release may reacquire the
    # lock. Never let an older release overwrite a later healthy deployment.
    if action == 'rollback' and not lock.exists():
        last = state / 'last-success.json'
        if (not last.exists() or json.loads(last.read_text())['release'] != release_id
                or not (release / 'dirty').exists()):
            raise ValueError('Only the latest successful release can be recovered')
        lock.mkdir(mode=0o700)
        (lock / 'owner').write_text(release_id)
    if lock.is_symlink() or not lock.is_dir() or (lock / 'owner').read_text() != release_id:
        raise ValueError('Deployment lock is not owned by this release')
    if release.is_symlink() or not release.is_dir():
        raise ValueError('Invalid release directory')

    if action == 'activate':
        if (release / 'ready.json').exists():
            raise ValueError('Release has already been activated')
        archive = release / 'payload.tar'
        if digest(archive) != request['archive_sha256']:
            raise ValueError('Uploaded archive checksum mismatch')
        staging = release / 'staging'
        staging.mkdir(mode=0o700)
        with tarfile.open(archive, 'r:') as bundle:
            seen = set()
            for entry in bundle:
                safe_name(entry.name)
                if not entry.isfile() or entry.name in seen:
                    raise ValueError('Archive contains an unsafe entry')
                seen.add(entry.name)
                target = staging / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(entry) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
        manifest = json.loads((staging / 'deployment.json').read_text())
        files = manifest['files']
        if not re.fullmatch(r'[0-9a-f]{40}', manifest['commit']):
            raise ValueError('Invalid commit')
        if set(files) | {'deployment.json'} != seen or 'index.html' not in files:
            raise ValueError('Archive does not match manifest')
        for name, expected in files.items():
            if digest(safe_file(staging, name)) != expected:
                raise ValueError('Staged file checksum mismatch')
        current = state / 'current.json'
        previous = json.loads(current.read_text()) if current.exists() else None
        old_files = previous['files'] if previous else {}
        managed = sorted(set(old_files) | set(files) | {'deployment.json'})
        backup = release / 'backup'
        backup.mkdir(mode=0o700)
        snapshot = {}
        # Complete and validate the snapshot before the first live-file mutation.
        for name in managed:
            existing = safe_file(destination, name)
            snapshot[name] = digest(existing) if existing.exists() else None
            if existing.exists():
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(existing, target)
                if digest(target) != snapshot[name]:
                    raise ValueError('Backup checksum mismatch')
        write_json(release / 'ready.json', {'snapshot': snapshot, 'previous': previous})
        (release / 'dirty').touch()
        for name in sorted(files) + ['deployment.json']:
            target = safe_file(destination, name)
            # Public directories are 755; private staging and backups remain 700.
            pending = []
            parent = target.parent
            while not parent.exists():
                pending.append(parent)
                parent = parent.parent
            for directory in reversed(pending):
                directory.mkdir(mode=0o755)
                directory.chmod(0o755)
            install(staging / name, target)
        for name in set(old_files) - set(files):
            target = safe_file(destination, name)
            if target.exists():
                target.unlink()
        # Verify every installed byte before allowing HTTP verification.
        for name in list(files) + ['deployment.json']:
            if digest(safe_file(destination, name)) != digest(staging / name):
                raise ValueError('Installed file checksum mismatch')
        return {'commit': manifest['commit']}

    if action == 'rollback':
        if (release / 'dirty').exists():
            snapshot = json.loads((release / 'ready.json').read_text())
            # Validate all backups first, so corrupt backups never start recovery.
            for name, expected in snapshot['snapshot'].items():
                safe_file(destination, name)
                if expected is not None and digest(release / 'backup' / name) != expected:
                    raise ValueError('Backup is damaged; manual recovery required')
            for name, expected in snapshot['snapshot'].items():
                target = safe_file(destination, name)
                if expected is None:
                    if target.exists():
                        target.unlink()
                else:
                    install(release / 'backup' / name, target)
                    if digest(target) != expected:
                        raise ValueError('Restoration checksum mismatch')
            if snapshot['previous'] is None:
                if (state / 'current.json').exists():
                    (state / 'current.json').unlink()
            else:
                write_json(state / 'current.json', snapshot['previous'])
            (release / 'dirty').unlink()
        # Backups are deliberately retained, including after recovery.
        status = 'rolled-back'
    elif action == 'finish':
        manifest = json.loads((release / 'staging' / 'deployment.json').read_text())
        write_json(state / 'current.json', manifest)
        write_json(state / 'last-success.json', {'release': release_id, 'commit': manifest['commit']})
        status = 'healthy'
    else:
        raise ValueError('Unknown remote operation')
    if probe.exists():
        probe.unlink()
    (release / 'status').write_text(status)
    (lock / 'owner').unlink()
    lock.rmdir()
    return {'status': status}


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception:
        # SSH diagnostics and configuration values must not leak into logs.
        print('Remote operation failed; inspect private deployment state on the server.', file=sys.stderr)
        sys.exit(1)
