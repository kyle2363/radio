import React, { useEffect, useRef, useState } from 'react';

import { createCatalogPreparationState } from '../api/catalogPreparationState';



const hintStyle = { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6 };

const errors = {

  catalog_prepare_failed: 'Preparation failed. Try again when the server is ready.',

  catalog_prepare_timeout: 'Preparation timed out. Try again.',

  catalog_prepare_cancelled: 'Preparation cancelled. Prepare again to resume cached hashing.',

  ingest_active: 'Wait for library processing to finish, then prepare again.',

};



export default function MobileCatalogSettings() {

  const [view, setView] = useState({ status: null, checking: true, error: '' });

  const observer = useRef(null);

  useEffect(() => {

    const state = createCatalogPreparationState({

      readStatus: () => window.electron.getMobileCatalogStatus(),

      prepare: () => window.electron.prepareMobileCatalog(), cancel: () => window.electron.cancelMobileCatalog(), onChange: setView,

    });

    observer.current = state;

    state.load();

    return () => { state.dispose(); observer.current = null; };

  }, []);

  const { status, checking, error } = view;

  const running = status?.state === 'running';

  return <div style={{ marginTop: 20 }}>

    <div className="set-label">Phone catalog</div>

    <p style={hintStyle}>Prepare hashes, existing library metadata and vectors, then tap Import catalog in Android Settings → Server. This explicitly reads source audio without modifying it. Unchanged hashes are reused; the first pass may take a while. Library processing must finish first.</p>

    <button type="button" disabled={checking || running} onClick={() => observer.current?.start()}

      style={{ border: '1px solid var(--border)', borderRadius: 6, background: 'var(--bg-surface)', color: 'var(--text-primary)', padding: '6px 10px', fontSize: 11, cursor: 'pointer' }}>Prepare catalog</button>

    {running && <button type="button" disabled={checking} onClick={() => observer.current?.cancel()}>Cancel preparation</button>}

    {status?.hash_progress && <p style={hintStyle}>Content hashes: {status.hash_progress.completed.toLocaleString()} / {status.hash_progress.total.toLocaleString()} checked · {status.hash_progress.hashed.toLocaleString()} ready · {status.hash_progress.skipped.toLocaleString()} unavailable or changed</p>}

    <p role="status" style={hintStyle}>{running ? 'Preparing catalog…' : checking ? 'Checking catalog…' : status?.state === 'ready' ? 'Catalog ready to import.' : status?.state === 'idle' ? 'No prepared catalog.' : ''}</p>

    {status?.revision != null && <div style={hintStyle}>Revision {status.revision} · {status.song_count.toLocaleString()} songs · {status.vector_count.toLocaleString()} vectors</div>}

    {(error || status?.error) && <p role="alert" style={{ ...hintStyle, color: 'var(--danger, #f87171)' }}>{error || errors[status.error]}{status?.revision != null && ' The previous catalog is still available.'}</p>}

  </div>;

}
