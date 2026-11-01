// Memory only. Replacing/revoking a ticket fences late replies as well as
// removing the currently displayed one-time secret.
export function createPairingBundleState(onChange, now = Date.now) {
  let generation = 0;
  let current = null;
  let disposed = false;
  const clear = () => {
    generation++;
    current = null;
    if (!disposed) onChange(null);
  };
  return {
    begin() { clear(); return generation; },
    clear,
    accept(request, value) {
      if (disposed || request !== generation || !Number.isFinite(value?.expires_at) || value.expires_at * 1000 <= now()) return;
      current = value;
      onChange(value);
    },
    tick() { if (current && current.expires_at * 1000 <= now()) clear(); },
    dispose() { disposed = true; clear(); },
  };
}
