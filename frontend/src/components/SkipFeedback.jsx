import React, { useState } from 'react';
import { ArrowLeft, X } from 'lucide-react';
import useDismissTimer from '../hooks/useDismissTimer';

const REASONS = [
  { key: 'too_intense', label: 'Too intense' }, { key: 'too_slow', label: 'Too slow' },
  { key: 'too_samey', label: 'Too similar' }, { key: 'wrong_mood', label: 'Wrong mood' },
  { key: 'just_skip', label: 'Just not this song' },
];
const DURATIONS = [
  { key: '24h', label: 'Skip for 24 hours' }, { key: 'session', label: 'Skip this session' },
  { key: 'permanent', label: 'Never play this' },
];

function SkipFeedbackPrompt({ onReason, onDismiss }) {
  const [reason, setReason] = useState(null);
  const { remaining, ...interaction } = useDismissTimer({ duration: 10000, onDismiss, held: reason !== null });
  return <section className="feedback-surface skip-feedback" aria-label="Skip feedback" {...interaction}
    onKeyDown={e => { e.stopPropagation(); if (e.key === 'Escape') onDismiss(); }}>
    <div className="toast-row"><h2>{reason ? 'How long should we skip it?' : 'What wasn’t working?'}</h2>
      <button className="feedback-close" aria-label="Dismiss skip feedback" data-tooltip="Dismiss · Esc" onClick={onDismiss}><X size={14} aria-hidden="true" /></button>
    </div>
    <p className="feedback-hint">{reason ? REASONS.find(r => r.key === reason)?.label : 'Optional · Help tune your station'}</p>
    <div className="skip-options">{(reason ? DURATIONS : REASONS).map(item => <button key={item.key}
      onClick={() => reason ? onReason(reason, item.key) : setReason(item.key)}>{item.label}</button>)}</div>
    {reason && <button className="feedback-back" onClick={() => setReason(null)}><ArrowLeft size={12} aria-hidden="true" />Back to reasons</button>}
    {!reason && <div className="feedback-track" aria-hidden="true"><div style={{ width: `${remaining / 100}%` }} /></div>}
  </section>;
}
export default function SkipFeedback({ visible, ...props }) { return visible ? <SkipFeedbackPrompt {...props} /> : null; }
