// Offline bundle smoke only. Shopify CLI performs the actual extension build.
import {build} from 'esbuild';
import assert from 'node:assert/strict';
for(const entry of ['extensions/loyalty-account/src/Rewards.tsx','extensions/loyalty-order-status/src/OrderStatus.tsx','extensions/loyalty-order-status/src/ThankYou.tsx','extensions/loyalty-order-status/src/CheckoutOffers.tsx']) {
  // Use the project's discoverable tsconfig, just like the CLI bundler, rather
  // than masking a missing JSX config with special smoke-test-only options.
  const result=await build({entryPoints:[entry],bundle:true,write:false,metafile:true,format:'esm',platform:'browser',target:'es2022',minify:true});
  assert(!Object.keys(result.metafile.inputs).some(file=>/node_modules\/(?:ts-morph|@ts-morph|fast-glob|micromatch|braces)\//.test(file)), 'Build-time tooling must not enter shopper bundles');
  console.log(`${entry}: PASS (${result.outputFiles[0].contents.length} bytes, no files emitted)`);
}
