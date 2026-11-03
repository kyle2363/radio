export function createDismissTimer(duration, onDismiss, clock = {}) {
  const now = clock.now || (() => performance.now());
  const schedule = clock.schedule || setTimeout, cancel = clock.cancel || clearTimeout;
  let left = duration, started = null, handle = null, disposed = false;
  const pauses = new Set();
  const remaining = () => Math.max(0, left - (started === null ? 0 : now() - started));
  const stop = () => { left = remaining(); started = null; if (handle !== null) cancel(handle); handle = null; };
  const start = () => {
    if (disposed || pauses.size || started !== null) return;
    started = now();
    handle = schedule(() => { if (!disposed) { stop(); onDismiss(); } }, left);
  };
  return { start, remaining,
    pause(reason) { pauses.add(reason); stop(); },
    resume(reason) { pauses.delete(reason); start(); },
    dispose() { stop(); disposed = true; },
  };
}

export function appendToast(toasts, toast) {
  const duplicate = toasts.find(t => !t.onAction && !toast.onAction &&
    t.progress == null && toast.progress == null && t.message === toast.message && t.type === toast.type);
  return duplicate ? toasts.map(t => t === duplicate ? { ...toast, count: (t.count || 1) + 1 } : t) : [...toasts, toast];
}
