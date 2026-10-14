import {readFile,readdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
for(const name of ['loyalty-account','loyalty-order-status']) {
  const root=`extensions/${name}`;
  const en=JSON.parse(await readFile(`${root}/locales/en.default.json`,'utf8'));
  const vi=JSON.parse(await readFile(`${root}/locales/vi.json`,'utf8'));
  assert.deepEqual(Object.keys(en).sort(),Object.keys(vi).sort());
  for(const key of Object.keys(en)) {
    assert(en[key].trim()&&vi[key].trim(),key);
    for (const text of [en[key],vi[key]]) {
      assert(!/(?<!\{)\{\w+\}(?!\})/.test(text), `${name}.${key}: use Shopify {{placeholder}} interpolation`);
    }
    const slots=value=>(value.match(/\{\{\s*\w+\s*\}\}/g)||[]).sort();
    assert.deepEqual(slots(en[key]),slots(vi[key]),key);
  }
  const config=await readFile(`${root}/shopify.extension.toml`,'utf8');
  assert(!/^name\s*=\s*"t:/m.test(config), `${name}: extension name must be a display name`);
  for(const file of await readdir(`${root}/src`)) {
    const source=await readFile(`${root}/src/${file}`,'utf8');
    assert(!/innerHTML|outerHTML|dangerouslySetInnerHTML|localStorage|sessionStorage|document\.cookie|console\./.test(source),file);
  }
  console.log(`${name}: locale keys/placeholders and source safety PASS`);
}
for(const file of await readdir('customer-shared')) {
  const source=await readFile(`customer-shared/${file}`,'utf8');
  assert(!/innerHTML|outerHTML|dangerouslySetInnerHTML|localStorage|sessionStorage|document\.cookie|console\./.test(source),file);
}
