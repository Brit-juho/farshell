"""Epoch-bound authority metadata; legacy wire aliases are never rewritten."""
from __future__ import annotations

import hashlib
import json
import re
import time

import vt_paths
from .db import database,host_id,setting,set_setting,encode,Refused

KEY=re.compile(r'\$[0-9]+:[0-9]+')


def enabled():
    path=vt_paths.state_dir().resolve()/'session-catalog/catalog.sqlite3'
    if not path.exists():
        return False
    with database() as db:
        return setting(db,'authority_identity_version',0)==2


def enable():
    with database(write=True) as db:
        if setting(db,'authority_identity_version',0)!=2:
            set_setting(db,'authority_identity_version',2)
            set_setting(db,'authority_identity_enabled_at',time.time())


def keys(record):
    out={k for k in record.get("bound_sessions",[]) if isinstance(k,str) and KEY.fullmatch(k)}
    for field in ('subject_session','resource_session'):
        value=record.get(field)
        if isinstance(value,str) and KEY.fullmatch(value):
            out.add(value)
    for field in ('agent','target'):
        ref=record.get(field)
        if isinstance(ref,dict):
            alias=f"{ref.get('sid')}:{ref.get('created')}"
            if KEY.fullmatch(alias):
                out.add(alias)
    for field in ('subject','resource'):
        value=record.get(field,'')
        if isinstance(value,str) and ':local/' in value:
            alias=value.split(':local/',1)[1]
            if KEY.fullmatch(alias):
                out.add(alias)
    return out


def current(*,cached=False):
    from . import inventory
    with database() as db:
        host=host_id(db)
        if cached:
            return {r['physical_key']:{'host':host,'epoch':r['epoch'],'physical_key':r['physical_key']}
                    for r in db.execute("SELECT epoch,physical_key FROM runtimes WHERE ended IS NULL AND state='running'")}
    observed=inventory.observe()
    if not observed['complete']:
        raise Refused('identity_unverified',503)
    if not observed.get('epoch'):
        if observed['sessions']:
            raise Refused('identity_unverified',503)
        return {}
    return {r['physical_key']:{'host':host,'epoch':observed['epoch'],'physical_key':r['physical_key']}
            for r in observed['sessions']}


def matches(record,bindings):
    required=keys(record)
    if not required:
        return True
    stored=record.get('runtime_bindings')
    return (record.get('identity_version')==2 and isinstance(stored,dict)
            and all(key in bindings and stored.get(key)==bindings[key] for key in required))


def filter_grants(grants):
    if not any(keys(g) and g.get('effect')!='deny' for g in grants):
        return grants
    try:
        if not enabled():
            return grants
        bindings=current()
    except (OSError,ValueError,Refused):
        bindings={}
    return [g for g in grants if g.get('effect')=='deny' or matches(g,bindings)]


def stamp_new(docs,before):
    """Only newly approved grants get automatic bindings; adoption is never approval."""
    if not enabled():
        return
    previous={g.get('id') for g in json.loads(before['grants'])['items']}
    bindings=None
    for grant in docs['grants']['items']:
        required=keys(grant)
        if not required or grant.get('id') in previous or grant.get('identity_version')==2:
            continue
        via=str(grant.get('approved_via',''))
        if 'migrat' in via or 'migrat' in str(grant.get('approved_by','')):
            continue
        # Old ledger projections cannot acquire authority merely by being re-read.
        if str(grant.get('origin','')).startswith('legacy:delegations:'):
            continue
        if bindings is None:
            bindings=current()
        if all(key in bindings for key in required):
            grant.update(identity_version=2,runtime_bindings={key:bindings[key] for key in sorted(required)})


def fingerprint(record):
    return hashlib.sha256(encode(record).encode()).hexdigest()


def preview():
    from authz import store
    bindings=current()
    # Raw records, not the filtered authority view, are needed for explicit migration.
    raw,bad=store._read('grants')
    if bad:
        raise Refused('store_unavailable',503)
    records=[]
    for grant in raw['items']:
        required=keys(grant)
        if (not required or grant.get('effect')=='deny' or grant.get('state','active')!='active'
                or matches(grant,bindings)):
            continue
        did=grant.get('deleg')
        if did:
            import delegation
            item=next((x for x in delegation.list_all() if x.get('id')==did),None)
            if item:
                required.update(side.get('key') for side in (item.get('parent',{}),item.get('child',{})) if isinstance(side.get('key'),str))
        available=all(key in bindings for key in required)
        records.append({'id':grant['id'],'fingerprint':fingerprint(grant),
                        'bindings':{key:bindings[key] for key in sorted(required) if key in bindings},
                        'available':available,'scopes':grant.get('scopes',[]),
                        'subject':grant.get('subject'),'resource':grant.get('resource')})
    return {'version':2,'records':records[:200],'remaining':max(0,len(records)-200)}


def apply(plan,selected):
    from authz import store
    if (not isinstance(selected,list) or not selected or len(selected)>200
            or any(not isinstance(x,str) for x in selected) or len(set(selected))!=len(selected)):
        raise Refused('invalid_selection',400)
    reviewed={r['id']:r for r in plan['records'] if r['available']}
    if not set(selected)<=set(reviewed):
        raise Refused('invalid_selection',400)
    bindings=current()
    with store.txn() as docs:
        by_id={g['id']:g for g in docs['grants']['items']}
        for gid in selected:
            record=by_id.get(gid);review=reviewed[gid]
            if (not record or fingerprint(record)!=review['fingerprint']
                    or any(bindings.get(k)!=v for k,v in review['bindings'].items())):
                raise Refused('identity_migration_changed')
        for gid in selected:
            by_id[gid].update(identity_version=2,runtime_bindings=reviewed[gid]['bindings'],bound_sessions=sorted(reviewed[gid]['bindings']))
        # Keep the legacy share projection consistent without changing immutable aliases.
        from authz import share_svc,audit
        audit.append({'principal':'human:identity-review','action':'identity.migrate','resource':'authz:*',
                      'decision':'allow','detail':{'grants':selected}})
        share_svc._write_legacy(docs)
    return {'identity_version':2,'bound':selected}


def stamp_approved(record):
    if enabled() and keys(record):
        bindings=current()
        required=keys(record)
        if not all(key in bindings for key in required):
            raise Refused('identity_unverified')
        record.update(identity_version=2,runtime_bindings={key:bindings[key] for key in sorted(required)})
    return record


def peer_binding(value, physical_key):
    if (not isinstance(value, dict) or set(value) != {'host','epoch','physical_key'}
            or value.get('physical_key') != physical_key or not KEY.fullmatch(physical_key)
            or not isinstance(value.get('epoch'), str) or not 1 <= len(value['epoch']) <= 512):
        raise Refused('peer_identity_incompatible',403)
    from .db import identifier
    identifier(value.get('host'))
    return value


def require_ledger(item,role='parent'):
    if not enabled():
        return
    bindings=current()
    required={side.get('key') for side in (item.get('parent',{}),item.get('child',{})) if side.get('key')}
    required={item.get(side,{}).get('key') for side in ('parent','child') if not item.get(side,{}).get('host')}
    if not required or any(not isinstance(k,str) or not KEY.fullmatch(k) for k in required):
        raise Refused('peer_identity_incompatible',403)
    record={'bound_sessions':sorted(required),'identity_version':item.get('identity_version'),
            'runtime_bindings':item.get('runtime_bindings')}
    if matches(record,bindings):
        return
    from authz import store
    raw,bad=store._read('grants')
    gid=('dc-' if role=='parent' else 'dr-')+str(item.get('id',''))
    grant=next((g for g in raw['items'] if g.get('id')==gid and g.get('state','active')=='active'),None)
    if bad or not grant or not required<=keys(grant) or not matches(grant,bindings):
        raise Refused('identity_migration_required',403)


def visible_ledger(items):
    try:
        if not enabled():
            return items
        bindings=current()
        from authz import store
        raw,bad=store._read('grants')
        by_deleg={}
        if not bad:
            for grant in raw['items']:
                if grant.get('state','active')=='active' and matches(grant,bindings):
                    by_deleg.setdefault(grant.get('deleg'),[]).append(grant)
        visible=[]
        for item in items:
            required={item.get(side,{}).get('key') for side in ('parent','child')}
            record={'bound_sessions':[k for k in required if isinstance(k,str)],
                    'identity_version':item.get('identity_version'),'runtime_bindings':item.get('runtime_bindings')}
            if (all(isinstance(k,str) and KEY.fullmatch(k) and k in bindings for k in required)
                    and (matches(record,bindings) or any(required<=keys(g) for g in by_deleg.get(item.get('id'),[])))):
                visible.append(item)
        return visible
    except (OSError,ValueError,Refused):
        return []
