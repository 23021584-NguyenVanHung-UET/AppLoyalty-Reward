// Contract smoke checks against an ephemeral localhost mock; no Shopify traffic.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {scenarios,rejectionCodes} from './scenarios.mjs';
const child=spawn(process.execPath,[fileURLToPath(new URL('./server.mjs',import.meta.url))],{env:{...process.env,WIDGET_PREVIEW_PORT:'0'},stdio:['ignore','pipe','pipe']});
try {
  const base=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Preview startup timed out')),5000);
    child.once('error',reject);child.once('exit',()=>reject(Error('Preview exited before startup')));
    child.stdout.once('data',chunk=>{clearTimeout(timer);resolve(chunk.toString().match(/http:\/\/127\.0\.0\.1:\d+/)[0]);});
  });
  const request=async(path,body)=>{
    const response=await fetch(base+path,{signal:AbortSignal.timeout(10000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    return {status:response.status,headers:response.headers,data:await response.json()};
  };
  assert.equal((await fetch(base+'/')).status,200);
  assert.equal((await fetch(base+'/frame?lang=vi&mode=inline')).status,200);
  const cartFrame=await fetch(base+'/frame?lang=vi&surface=cart&cartLayout=grid');
  assert.equal(cartFrame.status,200);assert((await cartFrame.text()).includes('<lp-cart-offers'));
  assert.equal((await request('/cart.js')).data.item_count,2);
  assert.equal((await fetch(base+'/assets/loyalty-cart.js')).status,200);
  assert.equal((await fetch(base+'/assets/loyalty-cart.css')).status,200);
  for(const name of scenarios){
    await request('/scenario',{scenario:name});
    const program=await request('/proxy/program');assert.equal(program.status,name==='500'?500:200);
    if(name==='500')continue;
    assert.equal(program.headers.get('cache-control'),'no-store');
    assert.equal(program.data.enabled,name!=='program-disabled');
    const me=await request('/proxy/me');assert.equal(me.status,name==='logged-out'?401:200);
    if(name==='logged-out')continue;
    if(name==='code-statuses'){
      const codes=await request('/proxy/redemptions');
      assert.deepEqual(codes.data.results.map(row=>row.status),['ISSUED','USED','EXPIRED','CANCELLED']);
      assert(codes.data.results.slice(1).every(row=>!('code' in row)));
    }
    const body={reward_id:2,client_key:'local-test'};
    const result=await request('/proxy/redeem',body);
    if(name==='429'){assert.equal(result.status,429);continue;}
    if(rejectionCodes.includes(name)){assert.equal(result.data.code,name);continue;}
    const retried=await request('/proxy/redeem',body);assert.deepEqual(retried.data,result.data);
    if(name==='redeem-pending'||name==='pending-timeout'){
      assert.equal(result.status,202);
      const first=await request('/proxy/redemptions');assert.equal(first.data.results[0].code,undefined);
      await request('/proxy/redemptions');await request('/proxy/redemptions');
      const ready=await request('/proxy/redeem',body);assert.equal(ready.status,name==='redeem-pending'?200:202);
    }
  }
  console.log(`Preview smoke: PASS (${scenarios.length} scenarios, localhost only).`);
} finally {child.kill();}
