import { afterEach, describe, expect, it, vi } from 'vitest';
import en from '../extensions/loyalty-widget/locales/en.default.json';
import { createProxy, ProxyError, friendlyError, money, points, date, expiry, estimate,
  translate, discountLink, RedeemFlow, rejectionKeys, newKey } from '../extensions/loyalty-widget/assets/loyalty-core.js';
const strings = en.lp;
const reward = {id: 2};
const issued = {status: 200, data: {status: 'ISSUED', code: 'EXACT-ATTEMPT', expires_at: null}};
const pending = {status: 202, data: {status: 'PENDING'}};
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('same-origin App Proxy', () => {
  it('keeps locale prefix and sends only reward + idempotency key', async () => {
    const fetcher = vi.fn().mockResolvedValue({ok: true, status: 202, json: async () => pending.data});
    const api = createProxy('/vi/apps/loyalty/', fetcher);
    expect((await api('redeem', {body: {reward_id: 2, client_key: 'attempt'}})).status).toBe(202);
    expect(fetcher).toHaveBeenCalledWith('/vi/apps/loyalty/redeem', expect.objectContaining({method: 'POST', cache: 'no-store', redirect: 'error', body: '{"reward_id":2,"client_key":"attempt"}'}));
    expect(fetcher.mock.calls[0][1]).not.toHaveProperty('credentials');
    expect(() => createProxy('https://outside.invalid/proxy')).toThrow();
    await expect(api('customers')).rejects.toThrow('Invalid endpoint');
  });
  it('maps HTTP errors without exposing server messages', async () => {
    const api = createProxy('/apps/loyalty', async () => ({ok: false, status: 429, json: async () => ({code: 'RATE_LIMITED', message: 'secret server detail'})}));
    const error = await api('me').catch(error => error);
    expect(friendlyError(error, strings)).toBe(strings.error_rate);
    expect(error.message).not.toContain('secret');
  });
  it('supports abort before and during a fetch', async () => {
    const controller = new AbortController(), fetcher = vi.fn(); controller.abort();
    await expect(createProxy('/apps/loyalty', fetcher)('me', {signal: controller.signal})).rejects.toHaveProperty('name', 'AbortError');
    expect(fetcher).not.toHaveBeenCalled();
    const second = new AbortController();
    const request = createProxy('/apps/loyalty', (_url, {signal}) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('', 'AbortError')))))('me', {signal: second.signal});
    second.abort(); await expect(request).rejects.toHaveProperty('name', 'AbortError');
  });
  it('handles network and non-JSON responses', async () => {
    await expect(createProxy('/apps/loyalty', async () => {throw Error('private');})('me')).rejects.toHaveProperty('code', 'NETWORK');
    await expect(createProxy('/apps/loyalty', async () => ({status: 502, json: async () => {throw Error();}}))('me')).rejects.toHaveProperty('code', 'UNAVAILABLE');
  });
});
describe('formatting without float money arithmetic', () => {
  it('preserves decimal precision beyond Number.MAX_SAFE_INTEGER', () => {
    expect(money('9999999999999999.99', 'USD', 'en-US')).toContain('9,999,999,999,999,999.99');
    expect(money('10.00', 'USD', 'vi-VN')).toContain('10,00');
    expect(points(1234567, 'en')).toBe('1,234,567');
    expect(date('2026-10-12T00:00:00Z', 'en')).toBe(new Intl.DateTimeFormat('en', {dateStyle:'medium'}).format(new Date('2026-10-12T00:00:00Z')));
  });
  it('floors exact variant estimates and rejects invalid rates', () => {
    expect(estimate('1999', '10.00')).toBe('1');
    expect(estimate('999999999999999999999', '0.01')).toBe('999999999999999999999');
    for (const rate of ['0', '-1', 'invalid']) expect(estimate('1000', rate)).toBe('0');
  });
  it('formats expiry boundaries and safely encodes discount links', () => {
    const now = Date.parse('2026-10-12T00:00:00Z');
    expect(expiry(new Date(now).toISOString(), strings, 'en', now)).toBe(strings.expired);
    expect(expiry(new Date(now+1).toISOString(), strings, 'en', now)).toBe(strings.expires_one);
    expect(expiry(new Date(now+3*86400000).toISOString(), strings, 'en', now)).toContain('3');
    expect(expiry(null, strings, 'en', now)).toBe(strings.no_expiry);
    expect(discountLink('A/B &?', '/vi/')).toBe('/vi/discount/A%2FB%20%26%3F?redirect=%2Fvi%2Fcart');
    expect(() => discountLink('X', 'https://elsewhere.invalid/')).toThrow();
    expect(translate({sample:'%points% pts'}, 'sample', {points:'10'})).toBe('10 pts');
  });
  it('has a cryptographic UUID fallback', () => {
    vi.stubGlobal('crypto', {getRandomValues: array => array.fill(1)});
    expect(newKey()).toBe('01'.repeat(16));
  });
});
describe('redemption lifecycle', () => {
  it('blocks double click, retains key on retry, and changes key for a new opening', async () => {
    let reject;
    const api = vi.fn().mockImplementationOnce(() => new Promise((_resolve, no) => {reject = no;})).mockResolvedValue(issued);
    const flow = new RedeemFlow(api); flow.open(reward); const key = flow.key;
    const first = flow.confirm(); await flow.confirm(); expect(api).toHaveBeenCalledTimes(1);
    reject(new ProxyError(0,'NETWORK')); await first; expect(flow.state).toBe('error');
    await flow.confirm(); expect(flow.state).toBe('success');
    expect(api.mock.calls.map(call => call[1].body.client_key)).toEqual([key,key]);
    flow.open(reward); expect(flow.key).not.toBe(key);
  });
  it('polls at 2 seconds and verifies the exact attempt with the same key', async () => {
    vi.useFakeTimers();
    const api = vi.fn().mockResolvedValueOnce(pending)
      .mockResolvedValueOnce({data:{results:[{reference:'new',reward_id:2,status:'ISSUED',code:'OTHER-TAB'}]}})
      .mockResolvedValueOnce(issued);
    const refresh = vi.fn().mockResolvedValue(); const flow = new RedeemFlow(api, undefined, refresh); flow.open(reward);
    const task = flow.confirm(); await vi.advanceTimersByTimeAsync(1999); expect(api).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); await task;
    expect(flow.result.code).toBe('EXACT-ATTEMPT'); expect(flow.state).toBe('success');
    expect(api.mock.calls[2][1].body.client_key).toBe(flow.key); expect(refresh).toHaveBeenCalledTimes(2);
  });
  it('never treats another tab code as this attempt when the replay is pending', async () => {
    vi.useFakeTimers(); let posts = 0;
    const api = vi.fn(async name => name === 'redeem' ? (posts++,pending) : {data:{results:[{reference:'other',reward_id:2,status:'ISSUED',code:'OTHER-TAB'}]}});
    const flow = new RedeemFlow(api); flow.open(reward); const task = flow.confirm();
    await vi.advanceTimersByTimeAsync(30000); await task;
    expect(flow.state).toBe('delayed'); expect(flow.result).toBeNull(); expect(posts).toBe(2);
  });
  it('bounds polling to 30 seconds even if a GET stalls', async () => {
    vi.useFakeTimers(); const api = vi.fn().mockResolvedValueOnce(pending).mockImplementation((_name, {signal}) => new Promise((_resolve,reject) => signal.addEventListener('abort', () => reject(new DOMException('','AbortError')))));
    const flow = new RedeemFlow(api); flow.open(reward); const task = flow.confirm();
    await vi.advanceTimersByTimeAsync(30000); await task;
    expect(flow.state).toBe('delayed'); expect(flow.busy).toBe(false);
  });
  it('aborts polling on teardown without an error view', async () => {
    vi.useFakeTimers(); const flow = new RedeemFlow(vi.fn().mockResolvedValue(pending)); flow.open(reward);
    const task = flow.confirm(); await vi.advanceTimersByTimeAsync(100); flow.stop(); await task;
    expect(flow.error).toBeNull(); expect(flow.busy).toBe(false);
  });
  it.each([...Object.keys(rejectionKeys), 'UNKNOWN'])('handles %s with a friendly message and a stable retry key', async code => {
    const refresh = vi.fn().mockResolvedValue(), error = new ProxyError(code === 'UNSUPPORTED_REWARD' ? 422 : 409, code);
    const flow = new RedeemFlow(vi.fn().mockRejectedValue(error), undefined, refresh); flow.open(reward); const key = flow.key;
    await flow.confirm(); expect(flow.state).toBe('error'); expect(flow.key).toBe(key);
    expect(refresh).toHaveBeenCalledOnce(); expect(friendlyError(error,strings)).not.toContain(code);
  });
  it('handles 429 without retrying automatically', async () => {
    const api = vi.fn().mockRejectedValue(new ProxyError(429,'RATE_LIMITED')); const flow = new RedeemFlow(api); flow.open(reward);
    await flow.confirm(); expect(flow.state).toBe('error'); expect(api).toHaveBeenCalledOnce();
  });
});
