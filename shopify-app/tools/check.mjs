// Offline source checks; never invokes Shopify or a remote service.
import { readFile, readdir, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
const root = fileURLToPath(new URL('../extensions/loyalty-widget/', import.meta.url));
const read = file => readFile(path.join(root, file), 'utf8');
const flatten = (object, prefix = '') => Object.entries(object).flatMap(([key, value]) =>
  typeof value === 'object' ? flatten(value, prefix + key + '.') : [[prefix + key, value]]);
const locales = {};
for (const file of await readdir(path.join(root, 'locales'))) {
  const source = await read('locales/' + file);
  assert(Buffer.byteLength(source) < 15000, `${file}: exceeds locale size limit`);
  locales[file] = Object.fromEntries(flatten(JSON.parse(source)));
}
for (const [en, vi] of [['en.default.json', 'vi.json'], ['en.default.schema.json', 'vi.schema.json']]) {
  assert.deepEqual(Object.keys(locales[en]).sort(), Object.keys(locales[vi]).sort(), 'Translation keys differ');
  for (const key of Object.keys(locales[en])) {
    assert(locales[vi][key].trim(), `Missing translation: ${key}`);
    const placeholders = text => (text.match(/%[a-z_]+%/g) ?? []).sort();
    assert.deepEqual(placeholders(locales[en][key]), placeholders(locales[vi][key]), key);
  }
}
for (const file of await readdir(path.join(root, 'blocks'))) {
  const source = await read('blocks/' + file);
  const schema = JSON.parse(source.match(/{%\s*schema\s*%}([\s\S]*?){%\s*endschema\s*%}/)[1]);
  assert(['body', 'section'].includes(schema.target));
  const ids = schema.settings.map(setting => setting.id);
  assert.equal(new Set(ids).size, ids.length, `${file}: duplicate setting id`);
  for (const setting of schema.settings.filter(s => s.type === 'range')) {
    assert(setting.default >= setting.min && setting.default <= setting.max);
    assert((setting.max - setting.min) / setting.step <= 100);
    assert(Number.isInteger((setting.default - setting.min) / setting.step));
  }
  for (const key of JSON.stringify(schema).matchAll(/t:([\w.]+)/g)) {
    assert(locales['en.default.schema.json'][key[1]], `${file}: unknown schema translation ${key[1]}`);
  }
}
const snippet = await read('snippets/loyalty-strings.liquid');
for (const [key] of Object.entries(locales['en.default.json'])) assert(snippet.includes(`'${key}'`), `Unused locale key ${key}`);
let bytes = 0;
for (const file of await readdir(path.join(root, 'assets'))) {
  const source = await read('assets/' + file);
  bytes += (await stat(path.join(root, 'assets', file))).size;
  if (file.endsWith('.js')) {
    assert(!/innerHTML|outerHTML|localStorage|sessionStorage|document\.cookie|console\./.test(source), `${file}: unsafe browser API`);
    const result = spawnSync(process.execPath, ['--check', path.join(root, 'assets', file)], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  }
}
console.log(`Locales, schemas, safety and JS syntax: PASS. Shipping JS + CSS: ${bytes} bytes (${(bytes / 1024).toFixed(2)} KiB).`);
