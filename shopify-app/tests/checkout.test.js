import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {render,h} from 'preact';
import {act} from 'preact/test-utils';
import en from '../extensions/loyalty-order-status/locales/en.default.json';
import vn from '../extensions/loyalty-order-status/locales/vi.json';
vi.mock('@shopify/ui-extensions/preact',()=>({}));
import {CheckoutOffers} from '../extensions/loyalty-order-status/src/CheckoutOffers.tsx';

let root,api,fetcher,offers;
const flush=async()=>{await act(async()=>{for(let i=0;i<30;i++)await Promise.resolve();});};
function signal(value){const callbacks=new Set();return{value,subscribe(callback){callbacks.add(callback);return()=>callbacks.delete(callback);},set(value){this.value=value;callbacks.forEach(callback=>callback(value));}};}
function offer(extra={}){return{reference:'test',code:'TEST-10',name:'Original reward',description:'An original description',discount_type:'PERCENTAGE',discount_value:'10.00',min_order_amount:'50.00',currency:'USD',expires_at:null,...extra};}
const response=data=>({ok:true,status:200,json:async()=>data});
const click=async node=>{await act(async()=>node.dispatchEvent(new MouseEvent('click',{bubbles:true})));await flush();};
function mount(){root=document.createElement('div');document.body.append(root);act(()=>render(h(CheckoutOffers,{api}),root));}
beforeEach(()=>{
  offers=[offer()];
  api={extension:{editor:undefined},buyerIdentity:{customer:signal({id:'gid://shopify/Customer/301'})},
    discountCodes:signal([]),instructions:signal({discounts:{canUpdateDiscountCodes:true}}),
    cost:{totalAmount:signal({amount:100,currencyCode:'USD'})},sessionToken:{get:vi.fn(async()=> 'fixture-token')},
    i18n:{translate:(key,values={})=>en[key].replace(/\{\{\s*(\w+)\s*\}\}/g,(_match,key)=>values[key]??''),
      formatCurrency:(value,{currency})=>`${currency} ${Number(value).toFixed(2)}`,formatDate:()=> 'Oct 20, 2026'},
    applyDiscountCodeChange:vi.fn(async change=>{api.discountCodes.set([{code:change.code}]);return{type:'success'};})};
  fetcher=vi.fn(async url=>response(url.endsWith('/program')?{enabled:true}:{results:offers}));vi.stubGlobal('fetch',fetcher);
});
afterEach(()=>{if(root){render(null,root);root.remove();root=null;}vi.useRealTimers();vi.unstubAllGlobals();});
const applyButton=()=>[...root.querySelectorAll('s-button')].find(node=>node.textContent===en.checkout_apply);
describe('Checkout offers',()=>{
  it('shows owned code, description, discount and minimum, then applies at checkout without redeeming points',async()=>{
    mount();await flush();
    expect(root.textContent).toContain('TEST-10');expect(root.textContent).toContain('An original description');
    expect(root.textContent).toContain('Save 10.00%');expect(root.textContent).toContain('Minimum order: USD 50.00');
    await click(applyButton());
    expect(api.applyDiscountCodeChange).toHaveBeenCalledExactlyOnceWith({type:'addDiscountCode',code:'TEST-10'});
    expect(root.querySelector('s-banner').getAttribute('heading')).toBe(en.checkout_apply_success);expect(root.textContent).toContain(en.checkout_applied);
    expect(fetcher.mock.calls.map(([url])=>url.split('/').at(-1))).toEqual(['program','offers']);
    expect(fetcher.mock.calls.every(([,options])=>options.method==='GET')).toBe(true);
  });
  it('uses sample editor data without tokens, backend calls or applying a code',async()=>{
    api.extension.editor={};mount();await flush();expect(root.textContent).toContain('PREVIEW-10');
    expect(root.querySelector('s-banner').getAttribute('heading')).toBe(en.checkout_preview);await click(applyButton());
    expect(fetcher).not.toHaveBeenCalled();expect(api.sessionToken.get).not.toHaveBeenCalled();expect(api.applyDiscountCodeChange).not.toHaveBeenCalled();
  });
  it.each([['en',en],['vi',vn]])('fills legacy placeholders returned by the host in %s',async(language,strings)=>{
    api.i18n.translate=key=>strings[key].replace(/\{\{\s*(\w+)\s*\}\}/g,'{$1}');
    offers=[offer({expires_at:'2099-10-20T00:00:00Z'})];
    mount();await flush();
    expect(root.textContent).toContain(language==='en'?'Save 10.00%':'Giảm 10.00%');
    expect(root.textContent).toContain(language==='en'?'Minimum order: USD 50.00':'Đơn tối thiểu: USD 50.00');
    expect(root.textContent).toContain(language==='en'?'Valid until Oct 20, 2026':'Có hiệu lực đến Oct 20, 2026');
    expect(root.textContent).not.toMatch(/\{(?:value|amount|date)\}/);
  });
  it('fills unresolved double-brace placeholders and formats fixed-amount offers',async()=>{
    api.i18n.translate=key=>en[key];
    offers=[offer({discount_type:'FIXED_AMOUNT',discount_value:'5.00',expires_at:'2099-10-20T00:00:00Z'})];
    mount();await flush();
    expect(root.textContent).toContain('Save USD 5.00');
    expect(root.textContent).toContain('Minimum order: USD 50.00');
    expect(root.textContent).toContain('Valid until Oct 20, 2026');
    expect(root.textContent).not.toMatch(/\{(?:value|amount|date)\}/);
  });
  it('guests get sign-in instructions and no private requests',async()=>{
    api.buyerIdentity.customer.set(undefined);api.i18n.translate=key=>vn[key];mount();await flush();
    expect(root.textContent).toContain('Đăng nhập qua liên kết');expect(fetcher).not.toHaveBeenCalled();
  });
  it('reacts to account changes, clears old codes and ignores delayed responses for the previous customer',async()=>{
    mount();await flush();let release;
    fetcher.mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));
    await click([...root.querySelectorAll('s-button')].find(node=>node.textContent===en.checkout_refresh));
    await act(async()=>api.buyerIdentity.customer.set({id:'gid://shopify/Customer/302'}));
    offers=[offer({code:'OTHER-OWNER'})];await flush();
    release(response({enabled:true}));await flush();
    expect(root.textContent).not.toContain('TEST-10');expect(root.textContent).toContain('OTHER-OWNER');
    await act(async()=>api.buyerIdentity.customer.set(undefined));await flush();
    expect(root.textContent).not.toContain('OTHER-OWNER');expect(root.textContent).toContain(en.checkout_login);
  });
  it('hides wrong-currency and expired codes and renders descriptions as plain text',async()=>{
    offers=[offer({description:'<img src=x onerror=alert(1)>'}),offer({reference:'wrong',code:'OTHER-CURRENCY',currency:'EUR'}),
      offer({reference:'expired',code:'OLD-CODE',expires_at:new Date(Date.now()-1).toISOString()})];
    mount();await flush();expect(root.querySelector('img')).toBeNull();expect(root.textContent).toContain('<img src=x');
    expect(root.textContent).not.toContain('OTHER-CURRENCY');expect(root.textContent).not.toContain('OLD-CODE');
  });
  it('checks cart instructions and prevents reapplying an already-applied code regardless of case',async()=>{
    api.instructions.set({discounts:{canUpdateDiscountCodes:false}});mount();await flush();await click(applyButton());
    expect(api.applyDiscountCodeChange).not.toHaveBeenCalled();expect(root.textContent).toContain(en.checkout_cannot_apply);
    await act(async()=>{api.instructions.set({discounts:{canUpdateDiscountCodes:true}});api.discountCodes.set([{code:'test-10'}]);});await flush();
    expect(applyButton()).toBeUndefined();expect(root.textContent).toContain(en.checkout_applied);
  });
  it('blocks double clicks and shows localized failure without raw Shopify messages',async()=>{
    let release;api.applyDiscountCodeChange.mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
    mount();await flush();const button=applyButton();
    await act(async()=>{button.dispatchEvent(new MouseEvent('click'));button.dispatchEvent(new MouseEvent('click'));});
    expect(api.applyDiscountCodeChange).toHaveBeenCalledTimes(1);
    release({type:'error',message:'private untranslated debug details'});await flush();
    expect(root.querySelector('s-banner').getAttribute('heading')).toBe(en.checkout_apply_error);expect(root.textContent).not.toContain('private untranslated');
    expect(root.textContent).not.toContain(en.checkout_apply_success);
  });
  it('shows authentication/load errors, empty codes and disabled program states',async()=>{
    fetcher.mockResolvedValueOnce({ok:false,status:401,json:async()=>({code:'INVALID_SESSION'})});
    mount();await flush();expect(root.textContent).toContain(en.checkout_login);
    fetcher.mockResolvedValueOnce(response({enabled:false}));
    await click([...root.querySelectorAll('s-button')].find(node=>node.textContent===en.checkout_refresh));
    expect(root.textContent).toContain(en.checkout_disabled);
    fetcher.mockRejectedValueOnce(new Error('private error'));
    await click([...root.querySelectorAll('s-button')].find(node=>node.textContent===en.checkout_refresh));
    expect(root.querySelector('s-banner').getAttribute('heading')).toBe(en.checkout_load_error);expect(root.textContent).not.toContain('private error');
    offers=[];await click([...root.querySelectorAll('s-button')].find(node=>node.textContent===en.checkout_refresh));
    expect(root.textContent).toContain(en.checkout_empty);
  });
});
