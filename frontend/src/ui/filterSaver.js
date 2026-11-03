// Debounce edits, serialize writes, and acknowledge only the latest selection.
export function createFilterSaver({ save, onSaved, onError }) {
  let timer, pending = null, running = null, revision = 0, disposed = false;
  const flush = () => {
    clearTimeout(timer);
    if (running) return running;
    running = (async () => {
      while (pending) {
        const job = pending; pending = null;
        try {
          await save(job.value);
          if (!disposed && job.revision === revision) onSaved?.(job.value);
        } catch (error) {
          if (!disposed && job.revision === revision) onError?.(error);
        }
      }
    })().finally(() => { running = null; });
    return running;
  };
  return {
    submit(value) {
      if (disposed) return;
      pending = { value, revision: ++revision };
      clearTimeout(timer); timer = setTimeout(flush, 300);
    },
    flush,
    dispose() { disposed = true; return flush(); },
  };
}
