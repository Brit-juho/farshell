"""Single catalog authority for repository visibility, with lossless legacy import."""
from __future__ import annotations

import json
from i18n import tr

from .db import database, setting, set_setting, changed, Refused


def _initialize():
    import repo_store
    with repo_store._locked():
        with database() as db:
            if setting(db,'repository_visibility_imported',False):
                return
        # Reject corrupt preferences instead of turning a failed import into empty state.
        for path,field in ((repo_store._path(),'repos'),(repo_store._legacy_path(),'hidden')):
            if path.exists():
                try:
                    value=json.loads(path.read_text())
                    if not isinstance(value,dict) or not isinstance(value.get(field),list):
                        raise ValueError
                except (OSError,ValueError):
                    raise Refused('repository_preferences_unreadable',503) from None
                break
        items=repo_store._read_unlocked()
        with database(write=True) as db:
            if not setting(db,'repository_visibility_imported',False):
                set_setting(db,'repository_visibility',items)
                set_setting(db,'repository_visibility_imported',True)


def listing():
    _initialize()
    with database() as db:
        return list(setting(db,'repository_visibility',{}).values())


def set_hidden(path,hidden,host):
    import repo_store
    norm=repo_store._normalize(path)
    if not norm:
        return {'ok':False,'error':'empty_path','reason':tr('경로가 비어 있습니다')}
    if type(hidden) is not bool:
        raise Refused('invalid_hidden',400)
    _initialize()
    with database(write=True) as db:
        items=setting(db,'repository_visibility',{})
        rid=repo_store.repo_id(host,norm)
        old=items.get(rid)
        if hidden and old is None and len(items)>=repo_store.MAX_REPOS:
            return {'ok':False,'error':'too_many','reason':tr('저장소는 최대 {n}개까지 기록할 수 있습니다').format(n=repo_store.MAX_REPOS)}
        if old is not None or hidden:
            items[rid]={'id':rid,'host':host,'path':norm,'hidden':hidden}
            set_setting(db,'repository_visibility',items)
            if host==repo_store.DEFAULT_HOST:
                db.execute('UPDATE projects SET hidden=?,revision=revision+1 WHERE path=? AND hidden!=?',(int(hidden),norm,int(hidden)))
            changed(db,'project_visibility',rid)
        return {'ok':True,'hidden':[r['path'] for r in items.values() if r['hidden']]}
