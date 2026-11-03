export function connectToServer({ probe, onReady, onError, now = Date.now, schedule = setTimeout, cancel = clearTimeout }) {
  let stopped = false, timer;
  const start = now();
  const poll = async () => {
    try {
      const response = await probe();
      if (stopped) return;
      if (response.ok) { stopped = true; onReady(); return; }
    } catch { /* The server may still be starting. */ }
    if (stopped) return;
    if (now() - start >= 30000) {
      stopped = true;
      onError('Couldn’t connect to the desktop server. Check that it’s running, then try again.');
      return;
    }
    timer = schedule(poll, 500);
  };
  poll();
  return () => { stopped = true; cancel(timer); };
}
