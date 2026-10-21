// Poll only the selected child. Inject transport/state so cancellation is testable.
export function startSubagentPolling({ fetchPage, state, receive, error, visible = () => !document.hidden,
  later = setTimeout, cancel = clearTimeout }) {
  let closed = false, busy = false, timer = null, controller = null;
  let previous = state(), finals = 0, dirty = false;
  const schedule = (ms) => { cancel(timer); timer = later(() => { timer = null; void run(); }, ms); };
  async function run() {
    if (closed || !visible()) return;
    if (busy) { dirty = true; return; }
    busy = true;
    controller = new AbortController();
    const request = controller;
    let retry = true;
    try {
      const page = await fetchPage(request.signal);
      if (!closed && !request.signal.aborted) receive(page);
    } catch (e) {
      if (!closed && !request.signal.aborted) {
        error(e);
        retry = ![400, 403, 404, 409, 422].includes(e.status);
      }
    } finally {
      busy = false;
      if (closed || !visible()) return;
      if (dirty) { dirty = false; schedule(0); }
      else if (retry && state() === 'working') schedule(1500);
      else if (retry && finals < 3) schedule([500, 1500, 3000][finals++]);
    }
  }
  void run();
  return {
    refresh() { finals = 0; cancel(timer); void run(); },
    changed() {
      const next = state();
      if (next === previous) return;
      previous = next; finals = 0; cancel(timer); void run();
    },
    visibility() { cancel(timer); if (visible()) { finals = 0; void run(); } else controller?.abort(); },
    stop() { closed = true; cancel(timer); controller?.abort(); },
  };
}

export function mergeTranscript(previous, page) {
  const rows = page.reset ? [] : previous;
  const merged = new Map(rows.map((r) => [r.id, r]));
  for (const r of Array.isArray(page.items) ? page.items : []) {
    if (typeof r?.id === 'string' && ['user', 'assistant', 'tool_call', 'tool_result'].includes(r.kind)) merged.set(r.id, r);
  }
  return [...merged.values()].slice(-400);
}
