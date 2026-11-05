export function createNonOverlappingPoll(task) {
  let active = true;
  let inFlight = null;
  return {
    run() {
      if (!active) return Promise.resolve(undefined);
      if (inFlight) return inFlight;
      inFlight = Promise.resolve()
        .then(() => task(() => active))
        .finally(() => { inFlight = null; });
      return inFlight;
    },
    stop() { active = false; },
  };
}
