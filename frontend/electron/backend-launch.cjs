const path = require('path');
const fs = require('fs');

function resolveBackendLaunch({ packaged, resourcesPath, dirname, userDataDir, dataDir,
  platform = process.platform, exists = fs.existsSync }) {
  const backendDir = packaged
    ? path.join(resourcesPath, 'backend')
    : path.resolve(dirname, '..', '..', 'backend');
  const frontendDist = packaged ? path.join(resourcesPath, 'dist') : path.resolve(dirname, '..', '..', 'frontend', 'dist');
  const pythonVenv = path.join(backendDir, 'venv', platform === 'win32' ? 'Scripts' : 'bin', platform === 'win32' ? 'python.exe' : 'python');
  return {
    backendDir,
    cwd: backendDir,
    serverScript: path.join(backendDir, 'main.py'),
    frontendDist,
    env: packaged ? { RADIO_FRONTEND_DIST: frontendDist,
      ...(userDataDir ? { RADIO_DATA_DIR: dataDir || path.join(userDataDir, 'data') } : {}) } : {},
    python: exists(pythonVenv) ? pythonVenv : (platform === 'win32' ? 'python' : 'python3'),
  };
}

function reportBackendSpawnError(error, { showErrorBox, quit, stopWaiting = () => {} }) {
  console.error('[Backend] Unable to start:', error);
  stopWaiting();
  showErrorBox('Radio could not start',
    `Radio could not start its Python backend (${error.message}).\n\nInstall a compatible Python runtime and the backend requirements, or provide backend/venv with its dependencies, then restart Radio.`);
  quit();
}

module.exports = { resolveBackendLaunch, reportBackendSpawnError };
