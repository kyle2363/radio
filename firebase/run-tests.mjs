import { mkdirSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const source = dirname(fileURLToPath(import.meta.url));
const runtime = resolve(source, '../.test-runtime/android-checkpoint3b');
mkdirSync(runtime, { recursive: true });
const env = { ...process.env, FIREBASE_EMULATORS_PATH: resolve(source, '../.test-runtime/firebase-emulators') };
const bundledJava = 'C:\\Program Files\\Android\\Android Studio\\jbr';
if (process.platform === 'win32' && !env.JAVA_HOME && existsSync(join(bundledJava, 'bin/java.exe'))) env.JAVA_HOME = bundledJava;
const pathKey = Object.keys(env).find(key => key.toLowerCase() === 'path') || 'PATH';
if (env.JAVA_HOME) env[pathKey] = `${join(env.JAVA_HOME, 'bin')}${process.platform === 'win32' ? ';' : ':'}${env[pathKey] || ''}`;
const result = spawnSync(process.execPath, [join(source, 'node_modules/firebase-tools/lib/bin/firebase.js'),
  'emulators:exec', '--config', join(source, 'firebase.json'), '--project', 'demo-radio-discovery', '--only', 'firestore',
  `node --test "${join(source, 'discovery.rules.test.mjs')}"`], { cwd: runtime, env, stdio: 'inherit', windowsHide: true });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
