// 백그라운드 셸(서버 bg_shells.py → agent 상태 엔트리의 `shells`) — 화면 쪽 정규화. DOM을 모른다.
//
// 서버가 주는 것: { items: [{ id, state: 'running'|'done', description, command?, started_at, ended_at, pending }], checked_at }.
// 명령줄(command)은 이 맥 화면에만 온다(다른 맥 중계에서는 서버가 뺀다). 모양이 틀린 항목은 버린다.
// 「끝남」은 Claude Code가 턴 끝에 보내는 목록에서 빠진 것이라 늦게 반영된다 — 그래서 마지막 확인 시각을 같이 둔다.
import { t } from '../core/i18n.js';

export interface BgShell {
  id: string;
  running: boolean;
  /** 확인 전(실행 직후, 아직 Claude Code 목록으로 확인 안 됨). */
  pending: boolean;
  description: string;
  command: string | null;
  startedAt: number | null;
  endedAt: number | null;
}

export interface BgShells {
  running: BgShell[];
  ended: BgShell[];
  /** 마지막으로 Claude Code 목록을 받은 시각(초). 아직 없으면 null. */
  checkedAt: number | null;
}

const EMPTY: BgShells = { running: [], ended: [], checkedAt: null };
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown, max: number): string => (typeof v === 'string' ? v.slice(0, max) : '');

export function normalizeShells(raw: unknown): BgShells {
  if (!raw || typeof raw !== 'object') return EMPTY;
  const r = raw as { items?: unknown; checked_at?: unknown };
  if (!Array.isArray(r.items)) return EMPTY;
  const all: BgShell[] = [];
  for (const it of r.items.slice(0, 64)) {
    if (!it || typeof it !== 'object') continue;
    const x = it as Record<string, unknown>;
    if (typeof x.id !== 'string' || !x.id || (x.state !== 'running' && x.state !== 'done')) continue;
    all.push({
      id: x.id, running: x.state === 'running', pending: x.pending === true,
      description: str(x.description, 200), command: typeof x.command === 'string' && x.command ? x.command.slice(0, 300) : null,
      startedAt: num(x.started_at), endedAt: num(x.ended_at),
    });
  }
  const byStart = (a: BgShell, b: BgShell) => (a.startedAt ?? 0) - (b.startedAt ?? 0);
  return {
    running: all.filter((s) => s.running).sort(byStart),
    ended: all.filter((s) => !s.running).sort((a, b) => (b.endedAt ?? 0) - (a.endedAt ?? 0)),
    checkedAt: num(r.checked_at),
  };
}

/** 셸 한 줄의 이름 — 설명이 있으면 설명, 없으면 명령줄, 둘 다 없으면 「백그라운드 셸」. */
export function shellLabel(s: BgShell): string {
  return s.description || s.command || t('백그라운드 셸');
}

/** 「실행 중 2 · 끝남 1」 — 둘 다 0이면 빈 글. */
export function shellCounts(s: BgShells): string {
  const parts: string[] = [];
  if (s.running.length) parts.push(t('실행 중 {n}', { n: s.running.length }));
  if (s.ended.length) parts.push(t('끝남 {n}', { n: s.ended.length }));
  return parts.join(' · ');
}
