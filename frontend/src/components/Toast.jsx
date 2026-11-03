import React, { useEffect, useRef, useState, useCallback } from 'react';
import { CircleAlert, Check, Info, X } from 'lucide-react';
import useDismissTimer from '../hooks/useDismissTimer';

function Toast({ toast, onClose }) {
  const { message, type = 'error', progress, actionLabel, onAction, count = 1 } = toast;
  const [leaving, setLeaving] = useState(false);
  const exit = useRef(null), closed = useRef(false), closeRef = useRef(onClose);
  closeRef.current = onClose;
  const dismiss = useCallback(() => {
    if (closed.current) return;
    closed.current = true;
    setLeaving(true);
    exit.current = setTimeout(() => closeRef.current(), 160);
  }, []);
  useEffect(() => () => clearTimeout(exit.current), []);
  const { remaining, ...interaction } = useDismissTimer({
    duration: type === 'error' || onAction ? 8000 : 4000,
    enabled: progress == null && !leaving, onDismiss: dismiss,
  });
  const Icon = type === 'error' ? CircleAlert : type === 'success' ? Check : Info;
  const color = type === 'error' ? 'var(--danger)' : type === 'info' ? 'var(--info)' : 'var(--accent)';
  return <div className={`feedback-surface toast${leaving ? ' toast-leaving' : ''}`} {...interaction} onKeyDown={e => e.stopPropagation()}>
    <div className="toast-row">
      <Icon size={16} style={{ color, flexShrink: 0 }} aria-hidden="true" />
      <p className="toast-message" role={type === 'error' ? 'alert' : 'status'}>{message}{count > 1 && <span className="toast-count"> ×{count}</span>}</p>
      {progress != null && <span className="toast-count">{Math.round(progress)}%</span>}
      {actionLabel && onAction && <button className="feedback-action" onClick={() => { onAction(); dismiss(); }}>{actionLabel}</button>}
      <button className="feedback-close" aria-label="Dismiss notification" data-tooltip="Dismiss notification" onClick={dismiss}><X size={14} aria-hidden="true" /></button>
    </div>
    {progress != null && <div className="feedback-track" role="progressbar" aria-label={message} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, progress))}><div style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} /></div>}
  </div>;
}

export default function ToastContainer({ toasts, onRemove, children }) {
  return <div className="feedback-stack">{children}<div className="toast-stack" aria-label="Notifications">
    {toasts.slice(0, 3).map(toast => <Toast key={toast.id} toast={toast} onClose={() => onRemove(toast.id)} />)}
  </div></div>;
}
