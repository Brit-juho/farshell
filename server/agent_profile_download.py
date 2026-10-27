"""Explicit, bounded public-GitHub downloads into private immutable snapshots."""
from __future__ import annotations

import io
import gzip
import unicodedata
import ipaddress
import json
import re
import secrets
import shutil
import socket
import tarfile
import time
from pathlib import PurePosixPath
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

import agent_profiles as profiles
import fsguard
from i18n import tr

MAX_COMPRESSED = 16 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_FILES = 2000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate(repo, commit, subdir='.'):
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', repo) or any(p in ('.', '..') for p in repo.split('/')):
        raise profiles.Refused(tr("공개 GitHub 저장소를 owner/repo 형식으로 입력하세요"))
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise profiles.Refused(tr("고정된 40자리 commit SHA가 필요합니다"))
    if not isinstance(subdir, str) or len(subdir) > 512 or '\\' in subdir or '\0' in subdir or PurePosixPath(subdir).is_absolute() or '..' in PurePosixPath(subdir).parts:
        raise profiles.Refused(tr("플러그인 하위 경로가 올바르지 않습니다"))
    return repo, commit, str(PurePosixPath(subdir or '.'))


def _fetch(repo, commit):
    # Do not use the server's proxies, browser cookies or Git credential helpers.
    addresses = socket.getaddrinfo('codeload.github.com', 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise profiles.Refused(tr("공개 다운로드 주소를 확인하지 못했습니다"))
    url = 'https://codeload.github.com/' + repo + '/tar.gz/' + commit
    request = Request(url, headers={'Accept': 'application/gzip', 'User-Agent': 'farshell-plugin-download'})
    with build_opener(ProxyHandler({}), HTTPSHandler(), _NoRedirect()).open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('download-status')
        deadline, chunks, total = time.monotonic() + 30, [], 0
        while True:
            chunk = response.read(min(65536, MAX_COMPRESSED + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_COMPRESSED or time.monotonic() > deadline:
                raise profiles.Refused(tr("다운로드 크기 또는 시간 한도를 넘었습니다"))
            chunks.append(chunk)
    return b''.join(chunks)


class _BudgetReader:
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_EXPANDED + MAX_FILES * 2048
        self.deadline = time.monotonic() + 30

    def read(self, size=-1):
        if time.monotonic() > self.deadline:
            raise profiles.Refused(tr("다운로드 크기 또는 시간 한도를 넘었습니다"))
        value = self.stream.read(min(size, self.remaining + 1) if size >= 0 else self.remaining + 1)
        self.remaining -= len(value)
        if self.remaining < 0:
            raise profiles.Refused(tr("플러그인 파일 수 또는 크기가 한도를 넘었습니다"))
        return value


def _extract(raw, root, repo, commit, subdir):
    from agent_profile_migration import _write_bytes
    if len(raw) > MAX_COMPRESSED:
        raise profiles.Refused(tr("다운로드 크기 또는 시간 한도를 넘었습니다"))
    prefix = repo.split('/')[1].lower() + '-' + commit
    selected = PurePosixPath(subdir).parts
    count = total = copied = 0
    seen = set()
    deadline = time.monotonic() + 30
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream, tarfile.open(fileobj=_BudgetReader(stream), mode='r|') as archive:
        for entry in archive:
            count += 1
            if count > MAX_FILES or time.monotonic() > deadline:
                raise profiles.Refused(tr("플러그인 파일 수 또는 크기가 한도를 넘었습니다"))
            name = entry.name
            path = PurePosixPath(name)
            if len(name) > 1024 or '\\' in name or '\0' in name or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0].lower() != prefix:
                raise profiles.Refused(tr("다운로드 압축 파일의 경로가 올바르지 않습니다"))
            if entry.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE) or entry.sparse is not None:
                raise profiles.Refused(tr("다운로드 압축 파일에 링크 또는 특수 파일이 있습니다"))
            key = unicodedata.normalize("NFC", str(path)).casefold()
            if key in seen:
                raise profiles.Refused(tr("다운로드 압축 파일의 경로가 충돌합니다"))
            seen.add(key)
            if entry.isfile():
                total += entry.size
                if entry.size < 0 or total > MAX_EXPANDED:
                    raise profiles.Refused(tr("플러그인 파일 수 또는 크기가 한도를 넘었습니다"))
            parts = path.parts[1:]
            if parts[:len(selected)] != selected or len(parts) <= len(selected):
                continue
            relative = str(PurePosixPath(*parts[len(selected):]))
            target = fsguard.profile_file(root, relative)
            if entry.isdir():
                fsguard.private_profile_directory(target)
            else:
                fsguard.private_profile_directory(target.parent)
                source = archive.extractfile(entry)
                if source is None:
                    raise ValueError('missing-archive-content')
                content = source.read(MAX_EXPANDED + 1)
                if len(content) != entry.size:
                    raise ValueError('invalid-archive-size')
                _write_bytes(target, content, 0o700 if entry.mode & 0o111 else 0o600)
                copied += 1
    if not copied:
        raise profiles.Refused(tr("선택한 경로에 플러그인 파일이 없습니다"))
    return copied


def fetch(profile_id, revision, repo, commit, subdir='.'):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    profiles.require_no_pending_jobs(profile_id)
    repo, commit, subdir = validate(repo, commit, subdir)
    base = fsguard.private_profile_directory(profiles.directory() / 'plugin-downloads')
    if sum(1 for _ in base.iterdir()) >= 16:
        raise profiles.Refused(tr("다운로드 보관본 수가 한도를 넘었습니다"))
    identifier = secrets.token_hex(16)
    folder = fsguard.private_profile_directory(base / identifier)
    tree = fsguard.private_profile_directory(folder / 'tree')
    jid = profiles.job_begin(profile_id, 'plugin-fetch')
    try:
        count = _extract(_fetch(repo, commit), tree, repo, commit, subdir)
        from agent_profile_plugins import _snapshot, _fingerprint
        digest = _fingerprint(_snapshot(tree))
        metadata = {'profile_id': profile_id, 'repo': repo, 'commit': commit, 'subdir': subdir,
                    'digest': digest, 'download_id': identifier, 'files': count}
        from agent_profile_migration import _write_bytes
        _write_bytes(folder / 'receipt.json', json.dumps(metadata).encode())
        return profiles.complete_job(profile_id, revision, jid, {**metadata, 'status': 'downloaded', 'install_review_required': True}, bump_revision=False)
    except BaseException as exc:
        try:
            shutil.rmtree(folder)
        finally:
            profiles.job_finish(jid, 'failed', {'downloaded': False})
        if isinstance(exc, (tarfile.TarError, EOFError)):
            raise profiles.Refused(tr("다운로드 압축 파일 형식이 올바르지 않습니다")) from None
        raise


def resolve(profile_id, download_id):
    folder = profiles.directory() / 'plugin-downloads' / profiles.identifier(download_id)
    metadata = json.loads(fsguard.read_profile_bytes(folder, 'receipt.json'))
    if not isinstance(metadata, dict) or metadata.get('profile_id') != profile_id:
        raise profiles.Refused(tr("검토한 다운로드 보관본을 찾지 못했습니다"))
    tree = fsguard.private_profile_directory(folder / 'tree', create=False)
    from agent_profile_plugins import _snapshot, _fingerprint
    if _fingerprint(_snapshot(tree)) != metadata.get('digest'):
        raise profiles.Refused(tr("검토한 플러그인 보관본이 바뀌었습니다"))
    return tree
