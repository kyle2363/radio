// Pure transport used by UI requests; ownership and playback stay in callers.
export function createRequest({ baseUrl, fetchImpl = globalThis.fetch, onUnauthorized = () => {}, getHeaders = () => ({}) }) {
  return async function request(method, path, body, timeout = 5000, externalSignal) {
    const controller = new AbortController();
    let timedOut = false;
    const externalAbort = () => controller.abort(externalSignal.reason);
    if (externalSignal?.aborted) externalAbort();
    else externalSignal?.addEventListener('abort', externalAbort, { once: true });
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);
    const options = { method, credentials: 'include', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...getHeaders() } };
    if (body !== undefined) options.body = JSON.stringify(body);
    try {
      const response = await fetchImpl(`${baseUrl}${path}`, options);
      if (response.status === 401) onUnauthorized();
      if (!response.ok) {
        const text = await response.text().catch(() => '');
        throw new Error(text || `${response.status} ${response.statusText}`);
      }
      return await response.json();
    } catch (error) {
      if (timedOut) throw new Error(`Request to ${path} timed out after ${timeout}ms`);
      throw error;
    } finally {
      clearTimeout(timer);
      externalSignal?.removeEventListener('abort', externalAbort);
    }
  };
}
