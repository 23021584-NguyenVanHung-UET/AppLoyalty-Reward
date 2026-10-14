import {afterEach,describe,it,expect,vi} from 'vitest';
import {captureReferral,createProxy} from '../extensions/loyalty-widget/assets/loyalty-core.js';
afterEach(()=>history.replaceState({},'', '/'));
describe('referral cart attribution',()=>{
  it('writes a validated code to the locale-aware Shopify cart once per page',async()=>{
    history.replaceState({},'','/?ref=h7k9m2np');
    const fetcher=vi.fn(async()=>({ok:true,json:async()=>({attributes:{loyalty_ref:'H7K9M2NP'}})}));
    expect(await captureReferral('/vi/',fetcher)).toBe(true);
    expect(await captureReferral('/vi/',fetcher)).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith('/vi/cart/update.js',expect.objectContaining({method:'POST',credentials:'same-origin',body:JSON.stringify({attributes:{loyalty_ref:'H7K9M2NP'}})}));
  });
  it.each(['?ref=bad','?ref=00000000','?ref=H7K9M2NP&ref=H7K9M2NP','?unrelated=H7K9M2NP'])('ignores malformed or ambiguous URLs: %s',async query=>{
    history.replaceState({},'', '/'+query);const fetcher=vi.fn();expect(await captureReferral('/',fetcher)).toBe(false);expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not send cart data to a different origin and handles failed attribution',async()=>{
    history.replaceState({},'','/?ref=H7K9M2NP');const fetcher=vi.fn(async()=>({ok:false}));
    expect(await captureReferral('https://other.example/',fetcher)).toBe(false);expect(fetcher).not.toHaveBeenCalled();
    expect(await captureReferral('/failed/',fetcher)).toBe(false);
  });
  it('verifies that Shopify accepted the cart attribute',async()=>{
    history.replaceState({},'','/?ref=H7K9M2NP');
    expect(await captureReferral('/missing/',async()=>({ok:true,json:async()=>({attributes:{}})}))).toBe(false);
  });
  it('proxy mutations contain only birthday fields, never customer identity',async()=>{
    const fetcher=vi.fn(async()=>({ok:true,status:200,json:async()=>({month:2,day:29})}));
    await createProxy('/apps/loyalty',fetcher)('profile/birthday',{body:{month:2,day:29}});
    expect(fetcher.mock.calls[0][0]).toBe('/apps/loyalty/profile/birthday');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({month:2,day:29});
  });
});
