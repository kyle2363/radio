import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { resolveBackendLaunch, reportBackendSpawnError } from '../electron/backend-launch.cjs';

test('packaged backend uses resources/backend and packaged Python lookup', () => {
  const result = resolveBackendLaunch({ packaged: true, resourcesPath: 'C:/Radio/resources', dirname: 'C:/Radio/resources/app.asar/electron', platform: 'win32', exists: p => p.endsWith('python.exe') });
  assert.equal(result.serverScript, path.join('C:/Radio/resources', 'backend', 'main.py'));
  assert.equal(result.cwd, path.join('C:/Radio/resources', 'backend'));
  assert.equal(result.python, path.join('C:/Radio/resources', 'backend', 'venv', 'Scripts', 'python.exe'));
  assert.equal(result.frontendDist, path.join('C:/Radio/resources', 'dist'));
  assert.deepEqual(result.env, { RADIO_FRONTEND_DIST: path.join('C:/Radio/resources', 'dist') });
});

// Filesystem paths use the host OS; platform selects the Python executable only.
// A Windows drive path is relative on the Linux CI runner.
for (const [platform, python] of [['win32', 'python'], ['linux', 'python3']]) {
  test(`development backend uses repository paths and ${platform} Python fallback`, () => {
    const repoDir = path.resolve('test-repo');
    const result = resolveBackendLaunch({ packaged: false,
      dirname: path.join(repoDir, 'frontend', 'electron'), platform, exists: () => false });
    assert.equal(result.serverScript, path.join(repoDir, 'backend', 'main.py'));
    assert.equal(result.cwd, path.join(repoDir, 'backend'));
    assert.equal(result.frontendDist, path.join(repoDir, 'frontend', 'dist'));
    assert.equal(result.python, python);
  });
}

test('packaged mutable data uses user storage and retains an explicit data override', () => {
  const options = { packaged: true, resourcesPath: 'C:/Program Files/Radio/resources',
    dirname: 'unused', userDataDir: 'C:/Users/test/AppData/Radio', exists: () => false };
  assert.equal(resolveBackendLaunch(options).env.RADIO_DATA_DIR, path.join(options.userDataDir, 'data'));
  assert.equal(resolveBackendLaunch({ ...options, dataDir: 'D:/RadioData' }).env.RADIO_DATA_DIR, 'D:/RadioData');
});

test('spawn errors surface a visible, actionable startup failure', () => {
  let message;
  let quit = false;
  let readinessCancelled = false;
  reportBackendSpawnError({ message: 'spawn python.exe ENOENT' }, {
    showErrorBox: (_title, body) => { message = body; },
    stopWaiting: () => { readinessCancelled = true; },
    quit: () => { quit = true; },
  });
  assert.match(message, /Python runtime/i);
  assert.match(message, /backend[\\/]venv/i);
  assert.equal(quit, true);
  assert.equal(readinessCancelled, true);
});
