import { useEffect, useRef } from "react";

export default function useFocusTrap(active) {
  const ref = useRef(null);

  useEffect(() => {
    if (!active || !ref.current) return;

    const trap = ref.current;
    const focusable = trap.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (first) first.focus();

    const handler = (e) => {
      if (e.key !== "Tab") return;
      const focusableNodes = trap.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      const f = focusableNodes[0];
      const l = focusableNodes[focusableNodes.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === f) {
          e.preventDefault();
          l?.focus();
        }
      } else {
        if (document.activeElement === l) {
          e.preventDefault();
          f?.focus();
        }
      }
    };

    trap.addEventListener("keydown", handler);
    return () => trap.removeEventListener("keydown", handler);
  }, [active]);

  return ref;
}
