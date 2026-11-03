export function applyFolderRange(selected, names, { fromIdx, toIdx, mode }) {
  const result = new Set(selected);
  for (let i = Math.min(fromIdx, toIdx); i <= Math.max(fromIdx, toIdx); i++) {
    const name = names[i];
    if (name == null) continue;
    if (mode) result.add(name); else result.delete(name);
  }
  return [...result].sort();
}

export function createFolderGesture(commit) {
  let range = null, suppressClick = false;
  return {
    begin(index, mode) { range = { fromIdx: index, toIdx: index, mode }; suppressClick = false; },
    enter(index) { if (range) range.toIdx = index; },
    release() {
      if (!range) return;
      const final = range;
      range = null; suppressClick = true;
      commit(final);
    },
    cancel() { range = null; suppressClick = false; },
    shouldToggle(detail) {
      if (detail === 0) return true;
      const should = !suppressClick;
      suppressClick = false;
      return should;
    },
  };
}
