"""Explicit raw-file recovery. Credential/configuration restore is not supported."""
from __future__ import annotations

import json
import os
from pathlib import Path

import fsguard
import vt_paths
from session_catalog.db import database, uid, identifier, Refused
from . import query, archive


def plan(mid):
    manifest = query.manifest(mid)
    info = json.loads(manifest['detail'])
    if info['state'] != 'saved':
        raise Refused('capture_incomplete')
    with database() as db:
        row = db.execute('SELECT s.* FROM source_files f JOIN sources s ON s.id=f.source_id WHERE f.id=?', (manifest['file_id'],)).fetchone()
        if not row or not row['enabled']:
            raise Refused('source_unavailable')
        source = dict(row)
    return {'manifest_id': mid, 'source_id': source['id'], 'source_revision': source['revision'],
            'root': source['root'], 'relative_path': info['relative_path'], 'provider': info['provider'],
            'bytes': manifest['length'], 'native_resume_verified': False}


def apply(plan):
    current = globals()['plan'](plan['manifest_id'])
    if current != plan:
        raise Refused('restore_plan_changed')
    manifest = query.manifest(plan['manifest_id'])
    target = fsguard.history_restore_target(plan['root'], plan['relative_path'], plan['provider'])
    if target.exists() or target.is_symlink():
        raise Refused('restore_target_exists')
    # Pin every parent directory from the allowed home; later path swaps cannot
    # redirect writes, links or cleanup outside the opened directory.
    home = vt_paths.user_home().resolve()
    relative = target.relative_to(home)
    directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = '.restore-' + uid()
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as out:
                archive.restore_stream(manifest, out)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temp, relative.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError:
                raise Refused('restore_target_exists') from None
            os.fsync(directory)
        finally:
            os.unlink(temp, dir_fd=directory)
    finally:
        os.close(directory)
    return {'state': 'files_restored', 'manifest_id': plan['manifest_id'], 'native_resume_verified': False}
