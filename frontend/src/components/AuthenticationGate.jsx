import React, { useEffect, useMemo, useState } from 'react';
import { createAuthClient, watchAuthentication } from '../api/auth.js';
import { API_BASE_URL } from '../api/index.js';

export default function AuthenticationGate({ children }) {
  const client = useMemo(() => createAuthClient(window.fetch.bind(window), API_BASE_URL), []);
  const [state, setState] = useState({ phase: 'checking' });
  const [attempt, setAttempt] = useState(0);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => watchAuthentication(client, setState), [client, attempt]);
  useEffect(() => {
    const recheck = () => { setState({ phase: 'checking' }); setPassword(''); setAttempt((value) => value + 1); };
    window.addEventListener('radio-auth-required', recheck);
    return () => window.removeEventListener('radio-auth-required', recheck);
  }, []);

  async function signIn(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await client.login(password);
      setState({ phase: 'ready' });
    } catch (failure) {
      setError(failure.message === 'Incorrect password. Try again.' ? failure.message : 'Unable to sign in. Try again.');
    } finally {
      setPassword('');
      setBusy(false);
    }
  }

  if (state.phase === 'ready') return children;

  return (
    <main className="min-h-screen flex items-center justify-center bg-[var(--bg-page)] text-[var(--text-primary)] p-6">
      <section className="w-full max-w-sm rounded-xl border border-[var(--border)] bg-[var(--bg-surface)] p-6" aria-label="Radio sign-in">
        <h1 className="text-xl font-semibold mb-2">Radio</h1>
        {state.phase === 'locked' ? (
          <form onSubmit={signIn}>
            <p className="text-sm mb-5">Enter your station password to continue.</p>
            <label className="block text-sm mb-2" htmlFor="station-password">Password</label>
            <input id="station-password" type="password" autoComplete="current-password" autoFocus required
              value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy}
              className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-2 mb-3" />
            {error && <p role="alert" className="text-sm text-red-400 mb-3">{error}</p>}
            <button type="submit" disabled={busy || !password} className="w-full rounded-md bg-teal-600 px-4 py-2 text-white disabled:opacity-50">
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        ) : <p role="status" className="text-sm">{state.phase === 'connecting' ? 'Waiting for the station… Retrying automatically.' : 'Connecting to the station…'}</p>}
      </section>
    </main>
  );
}
