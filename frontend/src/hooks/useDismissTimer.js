import { useEffect, useRef, useState } from 'react';
import { createDismissTimer } from '../ui/feedback.js';

export default function useDismissTimer({ duration, onDismiss, enabled = true, held = false }) {
  const callback = useRef(onDismiss);
  callback.current = onDismiss;
  const timer = useRef(null), hovered = useRef(false), focused = useRef(false);
  const [remaining, setRemaining] = useState(duration);
  useEffect(() => {
    if (!enabled) return;
    const current = createDismissTimer(duration, () => callback.current());
    timer.current = current;
    if (hovered.current) current.pause('hover');
    if (focused.current) current.pause('focus');
    if (held) current.pause('selection');
    current.start();
    setRemaining(duration);
    const interval = setInterval(() => setRemaining(current.remaining()), 100);
    return () => { current.dispose(); clearInterval(interval); timer.current = null; };
  }, [duration, enabled]);
  useEffect(() => {
    if (held) timer.current?.pause('selection'); else timer.current?.resume('selection');
  }, [held]);
  return { remaining,
    onMouseEnter: () => { hovered.current = true; timer.current?.pause('hover'); },
    onMouseLeave: () => { hovered.current = false; timer.current?.resume('hover'); },
    onFocusCapture: () => { focused.current = true; timer.current?.pause('focus'); },
    onBlurCapture: e => { if (!e.currentTarget.contains(e.relatedTarget)) { focused.current = false; timer.current?.resume('focus'); } },
  };
}
