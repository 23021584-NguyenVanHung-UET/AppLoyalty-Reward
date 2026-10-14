import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {render,h} from 'preact';
import {act} from 'preact/test-utils';
import {createCustomerAPI,loadOrderSummary,numericOrderId,orderMessage,RedeemFlow} from '../customer-shared/runtime.js';
import en from '../extensions/loyalty-account/locales/en.default.json';
import vn from '../extensions/loyalty-account/locales/vi.json';
import orderEn from '../extensions/loyalty-order-status/locales/en.default.json';
import {fixture} from '../../tools/widget-preview/scenarios.mjs';
vi.mock('@shopify/ui-extensions/preact',()=>({}));
import {RewardsPage} from '../extensions/loyalty-account/src/Rewards.tsx';
import {OrderSummary} from '../customer-shared/OrderSummary.tsx';
const response=(data,status=200)=>({ok:status<400,status,json:async()=>structuredClone(data)});
const flush=async()=>{await act(async()=>{for(let i=0;i<20;i++)await Promise.resolve();});};
let root;
afterEach(()=>{if(root){render(null,root);root.remove();root=null;}vi.useRealTimers();vi.unstubAllGlobals();});

describe('customer extension API',()=>{
  it('gets a token for every request, omits cookies and prevents redirects',async()=>{
    const session={get:vi.fn().mockResolvedValueOnce('local-token-one').mockResolvedValueOnce('local-token-two')};
    const fetcher=vi.fn(async()=>response({available:0}));
    const api=createCustomerAPI('https://backend.example',session,fetcher);
    await api('me');await api('history?page=2');
    expect(session.get).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][0]).toBe('https://backend.example/api/customer/me');
    expect(fetcher.mock.calls[0][1]).toMatchObject({credentials:'omit',redirect:'error',cache:'no-store',headers:{Authorization:'Bearer local-token-one'}});
    expect(fetcher.mock.calls[1][1].headers.Authorization).toBe('Bearer local-token-two');
  });
  it('posts only reward and key, maps machine error, and does not expose remote messages',async()=>{
    const fetcher=vi.fn(async()=>response({code:'INSUFFICIENT_POINTS',message:'private details'},409));
    const api=createCustomerAPI('https://backend.example',{get:async()=> 'local-token'},fetcher);
    await expect(api('redeem',{body:{reward_id:1,client_key:'key'}})).rejects.toMatchObject({code:'INSUFFICIENT_POINTS',status:409,message:'INSUFFICIENT_POINTS'});
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({reward_id:1,client_key:'key'});
  });
  it('restricts origin and endpoint so tokens cannot be sent to arbitrary URLs',async()=>{
    expect(()=>createCustomerAPI('http://backend.example',{get:vi.fn()})).toThrow();
    const get=vi.fn();const api=createCustomerAPI('https://backend.example',{get});
    for(const path of ['https://evil.example','../admin','me?customer_id=2','history?page=0','orders/1/points?customer_id=2'])await expect(api(path)).rejects.toThrow();
    expect(get).not.toHaveBeenCalled();
  });
  it('aborts a hanging token request and does not fetch afterwards',async()=>{
    const controller=new AbortController(),fetcher=vi.fn();let release;
    const api=createCustomerAPI('https://backend.example',{get:()=>new Promise(resolve=>{release=resolve;})},fetcher);
    const pending=api('me',{signal:controller.signal});controller.abort();
    await expect(pending).rejects.toMatchObject({name:'AbortError'});
    release('late-token');await flush();expect(fetcher).not.toHaveBeenCalled();
  });
  it('bounds a hanging request without logging or storing tokens',async()=>{
    vi.useFakeTimers();const getItem=vi.spyOn(Storage.prototype,'getItem'),setItem=vi.spyOn(Storage.prototype,'setItem');
    const log=vi.spyOn(console,'log');
    const api=createCustomerAPI('https://backend.example',{get:()=>new Promise(()=>{})},vi.fn());
    const result=api('me').catch(error=>error);await vi.advanceTimersByTimeAsync(10000);
    expect(await result).toMatchObject({code:'NETWORK'});expect(getItem).not.toHaveBeenCalled();expect(setItem).not.toHaveBeenCalled();expect(log).not.toHaveBeenCalled();
  });
  it('reuses the existing stable-key state machine with fresh tokens on retries',async()=>{
    const session={get:vi.fn(async()=> 'local-token')};let attempts=0;
    const fetcher=vi.fn(async()=>++attempts===1?response({code:'RATE_LIMITED'},429):response({status:'ISSUED',code:'LOCAL-CODE'}));
    const flow=new RedeemFlow(createCustomerAPI('https://backend.example',session,fetcher));
    flow.open({id:1});const key=flow.key;await flow.confirm();expect(flow.state).toBe('error');
    await Promise.all([flow.confirm(),flow.confirm()]);expect(flow.state).toBe('success');
    expect(fetcher).toHaveBeenCalledTimes(2);expect(session.get).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.map(([,options])=>JSON.parse(options.body).client_key)).toEqual([key,key]);
    flow.open({id:1});expect(flow.key).not.toBe(key);
  });
  it('verifies the exact 202 attempt with the same key and a fresh token before showing a code',async()=>{
    vi.useFakeTimers();let posts=0;
    const session={get:vi.fn(async()=> 'local-token')};
    const fetcher=vi.fn(async(url,options)=>{
      if(options.method==='POST')return ++posts===1?response({status:'PENDING'},202):response({status:'ISSUED',code:'LOCAL-VERIFIED'});
      return response({results:[{reference:'new-reference',reward_id:1,status:'ISSUED',code:'LOCAL-CANDIDATE'}]});
    });
    const flow=new RedeemFlow(createCustomerAPI('https://backend.example',session,fetcher));flow.open({id:1});
    const pending=flow.confirm();await vi.advanceTimersByTimeAsync(2000);await pending;
    expect(flow.state).toBe('success');expect(flow.result.code).toBe('LOCAL-VERIFIED');
    expect(session.get).toHaveBeenCalledTimes(3);
    const keys=fetcher.mock.calls.filter(([,options])=>options.method==='POST').map(([,options])=>JSON.parse(options.body).client_key);
    expect(keys[0]).toBe(keys[1]);
  });
});
describe('order summary polling',()=>{
  it('accepts only numeric order GIDs',()=>{
    expect(numericOrderId('gid://shopify/Order/123')).toBe('123');
    expect(numericOrderId('gid://shopify/Customer/123')).toBeNull();expect(numericOrderId(undefined)).toBeNull();
  });
  it('polls at most three more times, three seconds apart',async()=>{
    vi.useFakeTimers();const api=vi.fn(async endpoint=>response(endpoint==='program'?{enabled:true}:{status:'NOT_YET_PROCESSED',points:0}));
    // loadOrderSummary consumes the normalized API response, not fetch Responses.
    api.mockImplementation(async endpoint=>({data:endpoint==='program'?{enabled:true}:{status:'NOT_YET_PROCESSED',points:0},status:200}));
    const changed=vi.fn(),pending=loadOrderSummary(api,'123',new AbortController().signal,changed);
    await vi.advanceTimersByTimeAsync(8999);expect(api).toHaveBeenCalledTimes(4);
    await vi.advanceTimersByTimeAsync(1);expect((await pending).status).toBe('NOT_YET_PROCESSED');
    expect(api).toHaveBeenCalledTimes(5);await vi.advanceTimersByTimeAsync(30000);expect(api).toHaveBeenCalledTimes(5);
  });
  it('stops early when earned and hides disabled or ineligible orders',async()=>{
    vi.useFakeTimers();let reads=0;
    const api=vi.fn(async endpoint=>({data:endpoint==='program'?{enabled:true}:{status:++reads===1?'NOT_YET_PROCESSED':'EARNED_AVAILABLE',points:10}}));
    const pending=loadOrderSummary(api,'123',new AbortController().signal);await vi.advanceTimersByTimeAsync(3000);
    expect((await pending).points).toBe(10);expect(api).toHaveBeenCalledTimes(3);
    api.mockResolvedValue({data:{enabled:false}});expect(await loadOrderSummary(api,'123',new AbortController().signal)).toBeNull();
    api.mockImplementation(async endpoint=>({data:endpoint==='program'?{enabled:true}:{status:'NOT_ELIGIBLE'}}));
    expect(await loadOrderSummary(api,'123',new AbortController().signal)).toBeNull();
  });
  it('never fabricates points for 404 and cancels on unmount',async()=>{
    vi.useFakeTimers();const api=vi.fn(async endpoint=>{if(endpoint==='program')return {data:{enabled:true}};throw {status:404};});
    const pending=loadOrderSummary(api,'123',new AbortController().signal);await vi.advanceTimersByTimeAsync(9000);expect(await pending).toBeNull();
    const controller=new AbortController();const cancelled=loadOrderSummary(api,'123',controller.signal).catch(error=>error);
    await vi.advanceTimersByTimeAsync(1);controller.abort();expect(await cancelled).toMatchObject({name:'AbortError'});
  });
  it('maps every status to localized shopper copy',()=>{
    const t=(key,values={})=>orderEn[key]?.replace(/\{\{\s*(\w+)\s*\}\}/g,(_,key)=>values[key]);
    for(const status of ['EARNED_PENDING','EARNED_AVAILABLE','NOT_YET_PROCESSED','REVOKED'])
      expect(orderMessage({status,points:10,available_at:'2030-01-01'},t,String,String)).toBeTruthy();
    expect(orderMessage({status:'NOT_ELIGIBLE'},t,String,String)).toBe('');
  });
});

describe('shopper components with mocked Shopify runtime',()=>{
  let data,api,fetcher;
  beforeEach(()=>{
    root=document.createElement('div');document.body.append(root);data=fixture();
    data.codes=[{reference:'local-ref',reward_id:1,status:'ISSUED',code:'LOCAL-SECRET',expires_at:null}];
    api={sessionToken:{get:vi.fn(async()=> 'local-token')},i18n:{translate:(key,values={})=>en[key]?.replace(/\{\{\s*(\w+)\s*\}\}/g,(_,key)=>values[key])||key}};
    fetcher=vi.fn(async url=>{
      const endpoint=url.split('/').at(-1).split('?')[0];
      return response(endpoint==='program'?data.program:endpoint==='me'?data.me:endpoint==='rewards'?{results:data.rewards}:
        endpoint==='redemptions'?{results:data.codes}:endpoint==='history'?{page:1,pages:2,count:2,results:[{reference:'entry-1',type:'EARN',delta:10,date:'2030-01-01'}]}:{status:'ISSUED',code:'LOCAL-NEW'});
    });vi.stubGlobal('fetch',fetcher);
  });
  it.each([['en',en],['vi',vn]])('shows the tier grace warning from API qualifying shortfall in %s',async(language,strings)=>{
    data.me.tier=fixture('tier-grace').me.tier;data.me.available=999;
    api.i18n.translate=(key,values={})=>strings[key]?.replace(/\{\{\s*(\w+)\s*\}\}/g,(_match,name)=>values[name]??'')||key;
    act(()=>render(h(RewardsPage,{api}),root));await flush();
    const banner=root.querySelector('s-banner[tone="warning"]');
    expect(banner.getAttribute('heading')).toContain(language==='en'?'20 more qualifying points':'20 điểm xét hạng');
    expect(banner.getAttribute('heading')).toContain('Gold');
    expect(banner.getAttribute('heading')).not.toMatch(/\{(?:tier|points|date)\}/);
    data.me.tier.grace.active=false;
    act(()=>[...root.querySelectorAll('s-button')].find(node=>node.textContent===strings.refresh).dispatchEvent(new Event('click')));
    await flush();expect(root.querySelector('s-banner[tone="warning"]')).toBeNull();
  });
  it.each([['en',en],['vi',vn]])('renders API balances and reward terms using Shopify interpolation in %s',async(_locale,strings)=>{
    data.me.available=123;data.me.pending=45;data.program.pending_days=7;
    data.me.expiring_points=12;data.me.earliest_expiry='2030-01-01T00:00:00Z';
    data.rewards[0].discount_type='FIXED_AMOUNT';data.rewards[0].discount_value='5.00';
    data.rewards[0].min_order_amount='50.00';data.rewards[0].points_cost=200;
    data.rewards[0].affordable=false;
    api.i18n.translate=(key,values={})=>strings[key]?.replace(/\{\{\s*(\w+)\s*\}\}/g,(_match,name)=>values[name]??'')||key;
    act(()=>render(h(RewardsPage,{api}),root));await flush();
    const text=root.textContent;
    expect(text).toContain(_locale==='en'?'123 available points':'123 điểm khả dụng');
    expect(text).toContain(_locale==='en'?'45 pending points':'45 điểm đang chờ');
    expect(text).toContain(_locale==='en'?'200 pts':'200 điểm');
    expect(text).toContain('USD');expect(text).toContain('50.00');
    expect(text).not.toMatch(/\{(?:points|days|date|amount|discount)\}/);
    const banner=root.querySelector('s-banner');
    expect(banner.getAttribute('heading')).toContain('12');
    expect(banner.getAttribute('heading')).not.toContain('{date}');
  });
  it('escapes API names and descriptions and hides codes until explicitly opened',async()=>{
    data.rewards[0].name='<script>hostile()</script>';data.rewards[0].description='<img src=x onerror=hostile()>';
    act(()=>render(h(RewardsPage,{api}),root));await flush();
    expect(root.textContent).toContain('<script>hostile()</script>');expect(root.querySelector('script,img')).toBeNull();
    expect(root.textContent).not.toContain('LOCAL-SECRET');expect(root.querySelector('s-clipboard-item')).toBeNull();
    act(()=>[...root.querySelectorAll('s-button')].find(node=>node.textContent==='View code').dispatchEvent(new Event('click')));await flush();
    expect(root.textContent).toContain('LOCAL-SECRET');expect(root.querySelector('s-clipboard-item').getAttribute('text')).toBe('LOCAL-SECRET');
  });
  it('requires confirmation, submits once and paginates history',async()=>{
    act(()=>render(h(RewardsPage,{api}),root));await flush();
    const buttons=()=>[...root.querySelectorAll('s-button')];
    act(()=>buttons().find(node=>node.textContent==='Redeem').dispatchEvent(new Event('click')));await flush();
    expect(fetcher.mock.calls.filter(([,options])=>options.method==='POST')).toHaveLength(0);
    const confirm=buttons().find(node=>node.textContent==='Confirm');
    act(()=>{confirm.dispatchEvent(new Event('click'));confirm.dispatchEvent(new Event('click'));});await flush();
    expect(fetcher.mock.calls.filter(([,options])=>options.method==='POST')).toHaveLength(1);
    expect(root.textContent).toContain('LOCAL-NEW');
    act(()=>buttons().find(node=>node.textContent==='Load more').dispatchEvent(new Event('click')));await flush();
    expect(fetcher.mock.calls.some(([url])=>url.endsWith('history?page=2'))).toBe(true);
  });
  it('shows a retry state for failures and only program when disabled',async()=>{
    fetcher.mockRejectedValue(new Error('private provider details'));
    act(()=>render(h(RewardsPage,{api}),root));await flush();expect(root.querySelector('s-banner').getAttribute('heading')).toBe(en.error_title);expect(root.textContent).not.toContain('private provider');
    act(()=>render(null,root));data.program.enabled=false;
    fetcher.mockImplementation(async()=>response(data.program));fetcher.mockClear();
    act(()=>render(h(RewardsPage,{api}),root));await flush();expect(root.querySelector('s-banner').getAttribute('heading')).toBe(en.disabled);expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('silently hides the summary when the backend fails or the viewer is a guest',async()=>{
    fetcher.mockResolvedValue(response({code:'INVALID_SESSION'},401));
    act(()=>render(h(OrderSummary,{api,orderGid:'gid://shopify/Order/123'}),root));await flush();expect(root.textContent).toBe('');
  });
});
