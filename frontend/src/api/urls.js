// Build-time public configuration only; never put credentials in these URLs.
// API base may include a reverse-proxy prefix; WS override is the full endpoint.
export function resolveApiUrls(location, { apiBaseUrl = '', wsUrl = '' } = {}) {
  const defaultBase = location.protocol === 'file:'
    ? 'http://localhost:8765' : `${location.protocol}//${location.host}`;
  const base = parseUrl(apiBaseUrl.trim() || defaultBase, ['http:', 'https:'], 'API base URL');
  const apiBase = base.href.replace(/\/+$/, '');
  const explicitWs = wsUrl.trim();
  const socket = explicitWs
    ? parseUrl(explicitWs, ['ws:', 'wss:'], 'WebSocket URL')
    : new URL(`${apiBase}/ws/events`);
  if (!explicitWs) socket.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  return { apiBase, wsUrl: socket.href };
}

function parseUrl(value, protocols, name) {
  let url;
  try { url = new URL(value); }
  catch { throw new Error(`${name} must be an absolute URL`); }
  if (!protocols.includes(url.protocol)) throw new Error(`${name} must use ${protocols.join(' or ')}`);
  if (url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must not contain credentials, query parameters, or fragments`);
  }
  return url;
}
