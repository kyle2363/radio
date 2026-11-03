import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export default function TooltipLayer() {
  const id = useId(), node = useRef(null);
  const [tip, setTip] = useState(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  useEffect(() => {
    let timer, active = null;
    const clear = () => {
      clearTimeout(timer);
      if (active) {
        const ids = (active.getAttribute('aria-describedby') || '').split(' ').filter(x => x && x !== id);
        if (ids.length) active.setAttribute('aria-describedby', ids.join(' ')); else active.removeAttribute('aria-describedby');
      }
      active = null; setTip(null);
    };
    const show = e => {
      const target = e.target.closest?.('[data-tooltip]');
      if (!target || target === active) return;
      clear(); active = target;
      timer = setTimeout(() => {
        if (!target.isConnected) return clear();
        const text = target.dataset.tooltip;
        if (!text) return;
        const previous = target.getAttribute('aria-describedby');
        target.setAttribute('aria-describedby', [previous, id].filter(Boolean).join(' '));
        setTip({ target, text, theme: target.closest('[data-theme]')?.dataset.theme });
      }, e.type === 'focusin' ? 0 : 450);
    };
    const leave = e => { if (active && !active.contains(e.relatedTarget) && active.contains(e.target)) clear(); };
    const key = e => { if (e.key === 'Escape') clear(); };
    const observer = new MutationObserver(() => { if (active && !active.isConnected) clear(); });
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('mouseover', show); document.addEventListener('mouseout', leave);
    document.addEventListener('focusin', show); document.addEventListener('focusout', leave);
    document.addEventListener('keydown', key); document.addEventListener('pointerdown', clear);
    window.addEventListener('scroll', clear, true); window.addEventListener('resize', clear);
    return () => {
      clear();
      observer.disconnect();
      document.removeEventListener('mouseover', show); document.removeEventListener('mouseout', leave);
      document.removeEventListener('focusin', show); document.removeEventListener('focusout', leave);
      document.removeEventListener('keydown', key); document.removeEventListener('pointerdown', clear);
      window.removeEventListener('scroll', clear, true); window.removeEventListener('resize', clear);
    };
  }, [id]);
  useLayoutEffect(() => {
    if (!tip || !node.current) return;
    const rect = tip.target.getBoundingClientRect(), box = node.current.getBoundingClientRect();
    setPosition({ left: Math.max(8, Math.min(innerWidth - box.width - 8, rect.left + (rect.width - box.width) / 2)),
      top: rect.top >= box.height + 16 ? rect.top - box.height - 8 : Math.min(innerHeight - box.height - 8, rect.bottom + 8) });
  }, [tip]);
  return tip ? createPortal(<div ref={node} id={id} role="tooltip" data-theme={tip.theme} className="radio-tooltip" style={position}>{tip.text}</div>, document.body) : null;
}
