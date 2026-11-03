// One settings mount owns this bounded observer. Preparation continues on the
// server if settings closes; observing it never starts another export.
export function createCatalogPreparationState({ readStatus, prepare, cancel, onChange, schedule = setTimeout, unschedule = clearTimeout }) {
  let disposed = false, inFlight = false, timer = null, polls = 0;
  let view = { status: null, checking: false, error: '' };
  const publish = patch => { if (!disposed) { view = { ...view, ...patch }; onChange(view); } };
  const cancelTimer = () => { if (timer !== null) unschedule(timer); timer = null; };
  const observe = async (action, polling = false) => {
    if (disposed || inFlight) return;
    cancelTimer(); inFlight = true;
    publish({ checking: true, error: '' });
    try {
      let status;
      try { status = await action(); }
      catch (failure) {
        if (action !== prepare || disposed) throw failure;
        // A duplicate click or active ingest may reject the POST while the
        // bounded status endpoint still provides an actionable safe result.
        status = await readStatus();
        if (!['running', 'failed'].includes(status.state)) throw failure;
      }
      if (disposed) return;
      publish({ status, checking: false });
      if (status.state === 'running') {
        if (polling) polls++;
        if (polls >= 120) publish({ error: 'Catalog preparation is still running. Reopen Settings to check its status.' });
        else timer = schedule(() => observe(readStatus, true), 2500);
      }
    } catch {
      publish({ checking: false, error: 'Catalog preparation is unavailable. Check the desktop server and try again.' });
    } finally { inFlight = false; }
  };
  return {
    load: () => observe(readStatus),
    start() {
      if (disposed || inFlight || view.status?.state === 'running') return Promise.resolve();
      polls = 0;
      return observe(prepare);
    },
    cancel: () => observe(cancel),
    dispose() { disposed = true; cancelTimer(); },
  };
}
