import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// No credentials belong here: these are the exact public identifiers in Rules.
const [server, publisher, reader] = process.argv.slice(2);
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(server || '') ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(publisher || '') || !/^[A-Za-z0-9_-]{1,128}$/.test(reader || '') || publisher === reader) {
  throw Error('Usage: node firebase/render-rules.mjs SERVER_UUID PUBLISHER_UID READER_UID (distinct Firebase UIDs)');
}
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(root, '.test-runtime/android-checkpoint3b/firestore.rules');
const template = readFileSync(new URL('./firestore.rules', import.meta.url), 'utf8');
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, template.replaceAll('__SERVER_ID__', server).replaceAll('__PUBLISHER_UID__', publisher).replaceAll('__READER_UID__', reader));
console.log(`Prepared rules for review: ${output}`);
