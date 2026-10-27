// Only accepts the child owned by this Electron instance. Injectable I/O keeps
// tests independent of Electron, network services, and real process termination.
async function shutdownOwnedBackend(child, { request, forceKill, timeoutMs = 30000 }) {
  if (!child || !Number.isInteger(child.pid) || child.pid <= 0 ||
      child.exitCode != null || child.signalCode != null) return 'not-running';

  const result = await new Promise((resolve) => {
    let settled = false;
    let req;
    const finish = (reason) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeListener('exit', onExit);
      req?.destroy();
      resolve(reason);
    };
    const onExit = () => finish('exited');
    const timer = setTimeout(() => finish('timeout'), timeoutMs);
    child.once('exit', onExit);
    try {
      req = request({
        hostname: '127.0.0.1', port: 8765,
        path: '/api/shutdown', method: 'POST',
      }, (response) => {
        response.on('end', () => finish(response.statusCode >= 200 && response.statusCode < 300
          ? 'acknowledged' : 'error'));
        response.on('error', () => finish('error'));
        response.on('aborted', () => finish('error'));
        response.resume();
      });
      req.on('error', () => finish('error'));
      req.end();
    } catch {
      finish('error');
    }
  });
  // /api/shutdown drains workers and services but does not exit uvicorn.
  if (result !== 'exited' && child.exitCode == null && child.signalCode == null) {
    await forceKill(child);
  }
  return result;
}

module.exports = { shutdownOwnedBackend };
