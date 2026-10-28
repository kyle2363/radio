const DEV_URL = 'http://localhost:3000/';

function setThumbnailButtons(window, buttons, platform = process.platform, onError = () => {}) {
  if (platform !== 'win32' || typeof window?.setThumbarButtons !== 'function') return false;
  try { return window.setThumbarButtons(buttons); }
  catch (error) { onError(error); return false; }
}

async function loadDevelopmentWindow(window, browserSession, {
  isCancelled = () => false,
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
} = {}) {
  const cancelled = () => window.isDestroyed() || isCancelled();
  while (!cancelled()) {
    try {
      const response = await browserSession.fetch(DEV_URL, { cache: 'no-store', signal: AbortSignal.timeout(3000) });
      if (response.ok && !cancelled()) {
        await window.loadURL(DEV_URL);
        return true;
      }
    } catch { /* Vite may start later than Electron, or restart between probe and load. */ }
    if (!cancelled()) await sleep(500);
  }
  return false;
}

module.exports = { loadDevelopmentWindow, setThumbnailButtons };
