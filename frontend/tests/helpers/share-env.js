'use strict';
// 에이전트 세션 공유 화면 테스트 공용: jsdom 창 + 서버 흉내 fetch(요청 기록) + share/* 모듈 로드.
const path = require('node:path');
const { createDomEnv } = require('./dom-env');
const { importFresh } = require('./vm-esm');

const JS = path.join(__dirname, '../../js');

/** routes: { 'METHOD /path': (body, url) => ({status?, json}) } — 못 찾으면 404. 호출은 calls 에 쌓인다. */
async function loadShare(routes = {}, { files = ['share/entry.js'] } = {}) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  const { window } = env;
  const calls = [];
  const toasts = [];
  window.API_BASE = '';
  window._tokenQuery = '';
  window.showToast = (m, t) => toasts.push([m, t]);
  window.fitAndResize = () => {};
  window.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const p = String(url).split('?')[0];
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ method, url: String(url), body, opts });
    const h = routes[`${method} ${p}`];
    const r = h ? await h(body, String(url)) : { status: 404, json: { error: 'not_found' } };
    const status = r.status || 200;
    return { ok: status < 400, status, json: async () => r.json };
  };
  const cache = new Map();
  const mods = [];
  for (const f of files) mods.push(await importFresh(path.join(JS, f), env.context, cache));
  return { env, window, document: env.document, calls, toasts, mods, mod: mods[0], cache };
}

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms = 2000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { const v = fn(); if (v) return v; await tick(5); }
  throw new Error('waitFor: 타임아웃');
}

module.exports = { loadShare, tick, waitFor };
