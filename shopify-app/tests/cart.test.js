import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CartOffers, readyCodes } from '../extensions/loyalty-widget/assets/loyalty-cart.js';
import { discountLink } from '../extensions/loyalty-widget/assets/loyalty-core.js';
import en from '../extensions/loyalty-widget/locales/en.default.json';
import vn from '../extensions/loyalty-widget/locales/vi.json';

let widgets, fetcher, codes, count, enabled, sequence = 0;
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const row = (code, days = 2, extra = {}) => ({ status: 'ISSUED', code,
  expires_at: days === null ? null : new Date(Date.now() + days * 86400000).toISOString(), ...extra });
const config = extra => ({strings:en.lp,locale:'en',loggedIn:true,rootUrl:'/vi/',loginUrl:'/account/login',...extra});
function make(extra = {}) {
  const host = document.createElement('div'); host.dataset.proxy = `/cart-test-${++sequence}/apps/loyalty`;
  host.dataset.css = '/assets/loyalty-cart.css'; document.body.append(host);
  const widget = new CartOffers(host, config(extra)); widgets.push(widget); return widget;
}
beforeEach(() => {
  widgets = []; count = 2; enabled = true; codes = [row('LATER', 4), row('SOON', 1), row('NO-EXPIRY', null)];
  fetcher = vi.fn(async url => ({ok:true,status:200,json:async () => url.endsWith('/program')
    ? {enabled} : url.endsWith('/redemptions') ? {results:structuredClone(codes)} : {item_count:count}}));
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  widgets.forEach(widget => widget.destroy()); document.body.replaceChildren(); vi.useRealTimers(); vi.unstubAllGlobals();
});
describe('Cart offers', () => {
  it('loads only program, owned redemptions and cart; orders valid codes by expiry and preserves locale', async () => {
    codes.push(row('USED', 1, {status:'USED'}), row('EXPIRED', -1), row('CANCELLED', 1, {status:'CANCELLED'}), row('PENDING', 1, {status:'PENDING_CODE'}));
    const widget = make(); await flush();
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect([...widget.root.querySelectorAll('code')].map(node => node.textContent)).toEqual(['SOON','LATER','NO-EXPIRY']);
    expect(widget.root.querySelector('a').getAttribute('href')).toBe('/vi/discount/SOON?redirect=%2Fvi%2Fcart');
    expect(widget.root.querySelectorAll('a')[1].getAttribute('href')).toBe('/vi/discount/SOON?redirect=%2Fvi%2Fcheckout');
    expect(fetcher.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true);
    expect(fetcher.mock.calls.at(-1)[1]).toMatchObject({cache:'no-store',credentials:'same-origin',redirect:'error'});
  });
  it('filters malformed expiry, missing or duplicate codes and expiry at the exact boundary', () => {
    const now = Date.now();
    expect(readyCodes([row('OK'), row('ok'), row(''), row('X', 1, {expires_at:'bad'}),
      row('BOUNDARY', 1, {expires_at:new Date(now).toISOString()}), row('X'.repeat(65)), row('NO-EXPIRY', null)], now)
      .map(item => item.code)).toEqual(['OK', 'NO-EXPIRY']);
  });
  it('never requests private codes for a guest and returns login to the cart', async () => {
    const widget = make({loggedIn:false,strings:vn.lp,locale:'vi'}); await flush();
    expect(fetcher).toHaveBeenCalledTimes(1); expect(widget.body.textContent).toContain('Đăng nhập để xem');
    expect(widget.root.querySelector('a').getAttribute('href')).toBe('/account/login?return_url=%2Fvi%2Fcart');
    expect(widget.root.querySelector('code')).toBeNull(); expect(widget.refreshButton.hidden).toBe(true);
  });
  it('handles stale logged-in hints without exposing previous codes', async () => {
    const widget = make(); await flush();
    fetcher.mockImplementation(async url => url.endsWith('/redemptions')
      ? {ok:false,status:401,json:async()=>({code:'NOT_LOGGED_IN'})}
      : {ok:true,status:200,json:async()=>url.endsWith('/program')?{enabled:true}:{item_count:2}});
    await widget.load(); expect(widget.loggedIn).toBe(false); expect(widget.root.querySelector('code')).toBeNull();
    expect(widget.body.textContent).toContain(en.lp.cart_login);
  });
  it('does not list codes when the program is disabled or cart is empty', async () => {
    enabled = false; const disabled = make(); await flush();
    expect(fetcher).toHaveBeenCalledTimes(1); expect(disabled.body.textContent).toContain(en.lp.disabled);
    enabled = true; count = 0; const empty = make(); await flush();
    expect(empty.body.textContent).toContain(en.lp.cart_empty); expect(empty.root.querySelector('a')).toBeNull();
  });
  it('shows an empty state or hides it according to merchant settings', async () => {
    codes = []; const widget = make({appearance:{hideEmpty:true}}); await flush();
    expect(widget.host.hidden).toBe(true);
    widget.config.appearance.hideEmpty = false; widget.render();
    expect(widget.host.hidden).toBe(false); expect(widget.body.textContent).toContain(en.lp.cart_no_codes);
  });
  it('previews sample codes without requests, account data or actionable navigation', async () => {
    const widget = make({designMode:true,previewData:{codes:[row('PREVIEW-ONLY')]},appearance:{hideEmpty:true}}); await flush();
    expect(fetcher).not.toHaveBeenCalled(); expect(widget.body.textContent).toContain(en.lp.preview);
    expect(widget.root.querySelector('code').textContent).toBe('PREVIEW-ONLY');
    expect(widget.root.querySelector('a')).toBeNull(); expect(widget.body.querySelectorAll('button')).toHaveLength(2);
    expect([...widget.body.querySelectorAll('button')].every(button => button.disabled)).toBe(true);
  });
  it('applies merchant layout, limit, contrast, zero spacing and optional checkout button safely', async () => {
    const widget = make({appearance:{heading:'<img src=x onerror=alert(1)>',layout:'grid',limit:1,
      customColors:true,background:'#ffffff',foreground:'#ffffff',accent:'#ffffff',radius:0,spacing:0,showCheckout:false}});
    await flush();
    expect(widget.host.dataset.layout).toBe('grid'); expect(widget.root.querySelectorAll('code')).toHaveLength(1);
    expect(widget.root.querySelectorAll('a')).toHaveLength(1); expect(widget.root.querySelector('img')).toBeNull();
    expect(widget.root.querySelector('h2').textContent).toBe('<img src=x onerror=alert(1)>');
    expect(widget.host.style.getPropertyValue('--lc-text')).toBe('#000000');
    expect(widget.host.style.getPropertyValue('--lc-on-accent')).toBe('#000000');
    expect(widget.host.style.getPropertyValue('--lc-radius')).toBe('0px');
    expect(widget.host.style.getPropertyValue('--lc-space')).toBe('0px');
    expect(widget.body.textContent).toContain('2 more');
  });
  it('removes codes as soon as they expire without needing a refresh', async () => {
    vi.useFakeTimers(); codes = [row('SHORT', 1, {expires_at:new Date(Date.now()+1000).toISOString()})];
    const widget = make(); await flush(); expect(widget.root.querySelector('code')).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1000); expect(widget.root.querySelector('code')).toBeNull();
    expect(widget.body.textContent).toContain(en.lp.cart_no_codes);
  });
  it('refreshes after cart changes and cancels listeners on teardown', async () => {
    const widget = make(); await flush(); codes = [row('UPDATED')];
    document.dispatchEvent(new CustomEvent('cart:updated', {bubbles:true})); await flush();
    expect(widget.root.querySelector('code').textContent).toBe('UPDATED');
    widget.destroy(); const before = fetcher.mock.calls.length;
    window.dispatchEvent(new Event('pageshow')); await flush(); expect(fetcher).toHaveBeenCalledTimes(before);
  });
  it('clears codes on failed refresh and lets the buyer retry', async () => {
    const widget = make(); await flush(); fetcher.mockRejectedValueOnce(new Error('Network'));
    await widget.load(); expect(widget.root.querySelector('code')).toBeNull(); expect(widget.refreshButton.disabled).toBe(false);
    expect(widget.body.textContent).toContain(en.lp.error_network);
    widget.body.querySelector('button').click(); await flush(); expect(widget.root.querySelector('code')).not.toBeNull();
  });
  it('bounds a stalled request to ten seconds and ignores a superseded request', async () => {
    vi.useFakeTimers();
    fetcher.mockImplementationOnce((_url, {signal}) => new Promise((_resolve,reject) =>
      signal.addEventListener('abort',()=>reject(new DOMException('','AbortError')))));
    const widget = make(); await vi.advanceTimersByTimeAsync(10000); await flush();
    expect(widget.loading).toBe(false); expect(widget.body.textContent).toContain(en.lp.error_generic);
    let release;
    fetcher.mockImplementationOnce(() => new Promise(resolve => {release=resolve;}));
    const old = widget.load(); await flush(); await widget.load();
    release({ok:true,status:200,json:async()=>({enabled:false})}); await old;
    expect(widget.program.enabled).toBe(true); expect(widget.root.querySelector('code')).not.toBeNull();
  });
  it('mounts Shopify snippet-wrapped JSON and reinitializes cleanly when moved', () => {
    const node = document.createElement('lp-cart-offers'); node.dataset.proxy = '/proxy'; node.dataset.css = '/assets/loyalty-cart.css';
    const script = document.createElement('script'); script.type = 'application/json';
    script.textContent = JSON.stringify(config({designMode:true})).replace(JSON.stringify(en.lp),
      `<!-- BEGIN app snippet -->${JSON.stringify(en.lp)}<!-- END app snippet -->`);
    node.append(script); document.body.append(node); widgets.push(node.instance);
    expect(node.instance.root.querySelector('code')).not.toBeNull();
    const previous = node.instance; node.remove(); expect(previous.destroyed).toBe(true);
    document.body.append(node); widgets.push(node.instance); expect(node.instance).not.toBe(previous);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('encodes codes and restricts application links to the same storefront and supported destinations', () => {
    expect(discountLink('CODE/?<', '/vi/', 'checkout')).toBe('/vi/discount/CODE%2F%3F%3C?redirect=%2Fvi%2Fcheckout');
    for (const path of ['https://evil.example/', '//evil.example/', '/?next=evil', '/#evil'])
      expect(() => discountLink('CODE',path)).toThrow();
    expect(() => discountLink('CODE','/','https://evil.example')).toThrow();
    expect(discountLink('CODE')).toBe('/discount/CODE?redirect=%2Fcart');
  });
});
