import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import en from '../extensions/loyalty-widget/locales/en.default.json';
import viLocale from '../extensions/loyalty-widget/locales/vi.json';
import { LoyaltyWidget, PointsEstimate } from '../extensions/loyalty-widget/assets/loyalty-widget.js';
import { fixture } from '../../tools/widget-preview/scenarios.mjs';
let widgets, fetcher, data, sequence = 0, mobile;
const flush = async () => { for (let i=0;i<15;i++) await Promise.resolve(); };
function configuration(overrides = {}) {
  return {strings: en.lp, locale:'en', loggedIn:true, mode:'floating', radius:16, offset:24,
    rootUrl:'/vi/', loginUrl:'/account/login', registerUrl:'/account/register', tabs:{}, ...overrides};
}
function host() {
  const node=document.createElement('div'); node.dataset.proxy=`/test-${++sequence}/apps/loyalty`; node.dataset.css='/assets/loyalty-widget.css'; document.body.append(node); return node;
}
function make(overrides={}) { const widget=new LoyaltyWidget(host(), configuration(overrides)); widgets.push(widget); return widget; }
function mountEstimate(overrides={}) {
  const node=host(), widget=new PointsEstimate(node,{strings:en.lp,locale:'en',currency:'USD',variantId:1,variants:[{id:1,price:1999},{id:2,price:2999},{id:3,price:0}],...overrides}); widgets.push(widget);return widget;
}
beforeEach(() => {
  widgets=[];data=fixture();mobile={matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()};
  vi.stubGlobal('matchMedia',()=>mobile);
  fetcher=vi.fn(async (url, options={}) => {
    const endpoint=url.split('/').at(-1);
    const body=endpoint==='program'?data.program:endpoint==='me'?data.me:endpoint==='referral'?data.referral:endpoint==='birthday'?{...JSON.parse(options.body),editable:false,editable_at:'2027-10-13T00:00:00Z'}:endpoint==='code'?{code:'H7K9M2NP',share_link:'https://preview-shop.myshopify.com/?ref=H7K9M2NP',counts:{pending:0,rewarded:0,rejected:0},points_earned:0}:endpoint==='rewards'?{results:data.rewards}:endpoint==='redemptions'?{results:data.codes}:{status:'ISSUED',code:'LOCAL-ONLY',expires_at:null};
    return {ok:true,status:200,json:async()=>structuredClone(body)};
  });
  vi.stubGlobal('fetch',fetcher);
});
afterEach(() => { widgets.forEach(widget=>widget.destroy()); document.body.replaceChildren(); vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('birthday and referral widget',()=>{
  it.each([['en',en.lp],['vi',viLocale.lp]])('shows the grace shortfall and deadline in %s, then hides it after recovery',async(language,strings)=>{
    data=fixture('tier-grace');data.me.available=999;
    const widget=make({locale:language,strings});widget.open();await flush();
    const banner=widget.body.querySelector('.tier-grace');
    expect(banner.textContent).toContain('Gold');expect(banner.textContent).toContain('20');
    expect(banner.textContent).not.toMatch(/%(tier|points|date)%/);
    data.me.tier.grace.active=false;await widget.refresh();widget.render();
    expect(widget.body.querySelector('.tier-grace')).toBeNull();
  });
  it('requires confirmation and supports February 29 without collecting a year',async()=>{
    data=fixture('birthday-referral');const widget=make();widget.open();await flush();
    expect(widget.root.textContent).toContain(en.lp.birthday_title);
    const month=widget.root.querySelector('select[aria-label="Month"]'),day=widget.root.querySelector('select[aria-label="Day"]');
    const save=[...widget.root.querySelectorAll('button')].find(node=>node.textContent===en.lp.birthday_save);
    expect(save.disabled).toBe(true);month.value='2';month.dispatchEvent(new Event('change'));day.value='29';day.dispatchEvent(new Event('change'));
    const confirm=widget.root.querySelector('input[type="checkbox"]');confirm.checked=true;confirm.dispatchEvent(new Event('change'));
    expect(save.disabled).toBe(false);save.click();await flush();
    const call=fetcher.mock.calls.find(([url])=>url.endsWith('/profile/birthday'));
    expect(JSON.parse(call[1].body)).toEqual({month:2,day:29});
    expect(widget.me.birthday.editable).toBe(false);
  });
  it('shows referral config amounts, link and counts',async()=>{
    data=fixture('birthday-referral');const widget=make();widget.open();await flush();
    expect(widget.root.textContent).toContain(en.lp.referral_title);
    expect(widget.root.querySelector('.referral-link').value).toBe(data.referral.share_link);
    expect(widget.root.textContent).toContain('2 rewarded');
    expect(fetcher.mock.calls.some(([url])=>url.endsWith('/referral'))).toBe(true);
  });
  it('creates a referral link once through the signed proxy',async()=>{
    data=fixture('birthday-referral');data.referral={code:null,share_link:null,counts:{pending:0,rewarded:0,rejected:0},points_earned:0};
    const widget=make();widget.open();await flush();
    const create=[...widget.root.querySelectorAll('button')].find(node=>node.textContent===en.lp.referral_create);
    create.click();create.click();await flush();
    expect(fetcher.mock.calls.filter(([url])=>url.endsWith('/referral/code'))).toHaveLength(1);
    expect(widget.root.querySelector('.referral-link')).not.toBeNull();
  });
  it('hides both forms and makes no private calls for logged-out customers',async()=>{
    data=fixture('birthday-referral');const widget=make({loggedIn:false});widget.open();await flush();
    expect(widget.root.querySelector('.referral-link')).toBeNull();expect(widget.root.querySelector('select')).toBeNull();
    expect(fetcher.mock.calls.every(([url])=>url.endsWith('/program'))).toBe(true);
  });
  it('preview saves birthday without network calls and supports Vietnamese strings',async()=>{
    const widget=make({designMode:true,previewData:fixture('birthday-referral'),strings:viLocale.lp,locale:'vi'});widget.open();
    expect(widget.root.textContent).toContain(viLocale.lp.birthday_title);
    widget.birthdayDraft.confirm=true;await widget.saveBirthday();
    expect(widget.me.birthday.editable).toBe(false);expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('Shopify-rendered configuration', () => {
  function mount(tag, source) {
    const node=document.createElement(tag);
    node.dataset.proxy=`/test-${++sequence}/apps/loyalty`;node.dataset.css='/assets/loyalty-widget.css';
    const script=document.createElement('script');script.type='application/json';script.textContent=source;
    node.append(script);document.body.append(node);
    if(node.instance)widgets.push(node.instance);
    expect(node.instance).toBeDefined();
    return node.instance;
  }
  const wrapped = json => `<!-- BEGIN app snippet: loyalty-strings -->\n${json}\n<!-- END app snippet -->`;
  it.each(['floating','inline'])('mounts the %s panel with snippet comments around strings', mode => {
    const config=configuration({mode});
    const source=JSON.stringify(config).replace(JSON.stringify(en.lp),wrapped(JSON.stringify(en.lp)));
    const widget=mount('lp-loyalty',source);
    expect(widget.launcher.textContent).toContain(en.lp.launcher);
    expect(widget.panel.getAttribute('role')).toBe(mode==='inline'?'region':'dialog');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('accepts nested preview snippet comments without making requests', () => {
    const source=JSON.stringify(configuration({designMode:true,previewData:data}))
      .replace(JSON.stringify(data),wrapped(JSON.stringify(data)))
      .replace(JSON.stringify(en.lp),wrapped(JSON.stringify(en.lp)));
    const widget=mount('lp-loyalty',source);widget.open();
    expect(widget.body.textContent).toContain(en.lp.preview);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('keeps comment-like text, escaped quotes, backslashes and HTML inside JSON strings', () => {
    const label='Before "quote" \\ <!-- BEGIN app snippet: literal --> <b>Rewards</b> <!-- END app snippet -->';
    const source=JSON.stringify(configuration({label})).replace(JSON.stringify(en.lp),wrapped(JSON.stringify(en.lp)));
    const widget=mount('lp-loyalty',source);
    expect(widget.launcher.textContent).toBe(label);
    expect(widget.launcher.querySelector('b')).toBeNull();
  });
  it('mounts the product estimate with annotated translation JSON', () => {
    const config={strings:en.lp,locale:'en',currency:'USD',variantId:1,variants:[{id:1,price:1999}],designMode:true};
    const source=JSON.stringify(config).replace(JSON.stringify(en.lp),wrapped(JSON.stringify(en.lp)));
    const widget=mount('lp-estimate',source);
    expect(widget.text.hidden).toBe(false);
    expect(widget.text.textContent).toContain('1');
    expect(fetcher).not.toHaveBeenCalled();
  });
});
describe('lazy, private rendering', () => {
  it('shows tier progress from net qualifying points, independent of available balance', async () => {
    data=fixture('membership-tier');data.me.available=1;
    const widget=make({strings:viLocale.lp,locale:'vi'});widget.open();await flush();
    expect(widget.body.textContent).toContain('Silver');expect(widget.body.textContent).toContain('620');expect(widget.body.textContent).toContain('Gold');
    expect(widget.body.querySelector('.membership progress').value).toBe(280);
    data.me.tier.enabled=false;await widget.refresh();widget.render();expect(widget.body.querySelector('.membership')).toBeNull();
  });
  it('makes no default API calls until first opening, reuses loaded data', async () => {
    const widget=make(); expect(fetcher).not.toHaveBeenCalled(); widget.open(); await flush();
    expect(fetcher).toHaveBeenCalledTimes(4); expect(widget.body.textContent).toContain('380');
    widget.close(); widget.open(); await flush(); expect(fetcher).toHaveBeenCalledTimes(4);
    expect(widget.root.querySelector('style')).toBeNull(); expect(widget.root.querySelector('link').rel).toBe('stylesheet');
  });
  it('shares one optional idle /me request between widgets on a page', async () => {
    vi.useFakeTimers();const first=make({showPoints:true}), node=host();node.dataset.proxy=first.host.dataset.proxy;
    const second=new LoyaltyWidget(node,configuration({showPoints:true})); widgets.push(second);
    expect(fetcher).not.toHaveBeenCalled();await vi.advanceTimersByTimeAsync(1200);await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);expect(fetcher.mock.calls[0][0]).toMatch(/\/me$/);
    expect(first.badge.textContent).toContain('380'); expect(second.badge.hidden).toBe(false);
  });
  it('does not let a late idle badge overwrite the fresh account balance', async () => {
    vi.useFakeTimers(); let release;
    const idle = new Promise(resolve => { release = resolve; });
    fetcher.mockImplementationOnce(() => idle);
    const widget = make({showPoints:true});
    await vi.advanceTimersByTimeAsync(1200);
    data.me.available = 180;
    widget.open(); await flush();
    expect(widget.badge.textContent).toContain('180');
    release({ok:true,status:200,json:async()=>({available:380})}); await flush();
    expect(widget.badge.textContent).toContain('180');
  });
  it('logged-out customers fetch only public program and get localized return links', async () => {
    const widget=make({loggedIn:false});widget.open();await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);expect(widget.body.textContent).toContain('USD');
    expect(widget.body.querySelector('a').getAttribute('href')).toContain('return_url=');
    expect(widget.tabs.hidden).toBe(true);
  });
  it('handles a stale logged-in Liquid hint with NOT_LOGGED_IN', async () => {
    fetcher.mockImplementation(async url=>url.endsWith('/program')?{ok:true,status:200,json:async()=>data.program}:{ok:false,status:401,json:async()=>({code:'NOT_LOGGED_IN'})});
    const widget=make();widget.open();await flush();expect(widget.loggedIn).toBe(false);expect(widget.body.textContent).toContain(en.lp.login);
  });
  it('design mode uses sample data, translates it, and never redeems', async () => {
    const widget=make({designMode:true,previewData:data,strings:viLocale.lp,locale:'vi'});widget.open();widget.select('rewards');
    const redeem=[...widget.body.querySelectorAll('button')].find(button=>!button.disabled);redeem.click();
    expect(widget.body.textContent).toContain(viLocale.lp.preview);expect(widget.live.textContent).toBe(viLocale.lp.preview_no_redeem);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('also honors Shopify.designMode if the Liquid preview blob is missing', () => {
    vi.stubGlobal('Shopify',{designMode:true});const widget=make();widget.open();
    expect(widget.body.textContent).toContain(en.lp.preview);expect(fetcher).not.toHaveBeenCalled();
  });
  it('renders hostile API strings as text and never uses browser storage', async () => {
    const local=vi.spyOn(Storage.prototype,'setItem'), get=vi.spyOn(Storage.prototype,'getItem');
    data.rewards[0].name='<script>alert(1)</script>';data.rewards[1].description='<img src=x onerror=alert(1)>';
    const widget=make();widget.open();await flush();widget.select('rewards');
    expect(widget.body.textContent).toContain('<script>');expect(widget.body.querySelector('script,img')).toBeNull();
    expect(local).not.toHaveBeenCalled();expect(get).not.toHaveBeenCalled();
  });
  it('shows disabled and error states only on opening', async () => {
    data.program.enabled=false;const disabled=make();disabled.open();await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);expect(disabled.body.textContent).toContain(en.lp.disabled);
    fetcher.mockRejectedValue(Error('private stack'));const failed=make();expect(failed.body.textContent).toBe('');
    failed.open();await flush();expect(failed.body.textContent).toContain(en.lp.error_title);expect(failed.body.textContent).not.toContain('private');
  });
  it('explains pending days from API, debt, expiring points, and affordability', async () => {
    data.program.pending_days=14;data.me.debt=75;data.me.expiring_points=80;data.me.earliest_expiry='2030-01-01T00:00:00Z';
    const widget=make();widget.open();await flush();expect(widget.body.textContent).toContain('14');expect(widget.body.textContent).toContain(en.lp.debt);
    widget.select('rewards');expect([...widget.body.querySelectorAll('button')].every(button=>button.disabled)).toBe(true);
  });
});
describe('keyboard and redemption UI', () => {
  it('moves focus inside, wraps Tab, closes with ESC, and restores launcher focus', async () => {
    const widget=make();widget.open();await flush();expect(widget.root.activeElement).toBe(widget.closeButton);
    widget.closeButton.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
    const last=widget.root.activeElement;expect(last).not.toBe(widget.closeButton);
    last.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));expect(widget.root.activeElement).toBe(widget.closeButton);
    widget.flow.busy=true;widget.closeButton.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    expect(widget.opened).toBe(false);expect(widget.root.activeElement).toBe(widget.launcher);
  });
  it('supports roving tabs, hidden tabs, and RTL arrow navigation', async () => {
    const widget=make({tabs:{codes:false}});widget.host.style.direction='rtl';widget.open();await flush();
    const tab=widget.tabs.querySelector('[data-tab=points]');tab.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    expect(widget.tab).toBe('history');expect(widget.tabs.querySelector('[data-tab=history]').tabIndex).toBe(0);
    widget.root.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));expect(widget.tab).toBe('points');
    expect(widget.tabs.querySelector('[data-tab=codes]')).toBeNull();
  });
  it('keeps focus inside a dialog and opens only one floating dialog', async () => {
    const first=make();first.open();await flush();const outside=document.createElement('button');document.body.append(outside);outside.focus();
    expect(first.root.activeElement).toBe(first.closeButton);
    const second=make();second.open();await flush();expect(first.opened).toBe(false);expect(second.opened).toBe(true);
    expect(second.root.activeElement).toBe(second.closeButton);
  });
  it('refreshes codes after the delayed outcome before displaying them', async () => {
    const widget=make();widget.open();await flush();widget.flow.reward=data.rewards[1];widget.flow.state='delayed';widget.render();
    data.codes=[{status:'ISSUED',code:'NOW-READY',expires_at:null}];widget.body.querySelector('button').click();await flush();
    expect(widget.tab).toBe('codes');expect(widget.body.textContent).toContain('NOW-READY');
  });
  it('reloads My codes on explicit visits when a background issuance completes later', async () => {
    const widget=make();widget.open();await flush();widget.select('codes');await flush();
    expect(widget.body.textContent).toContain(en.lp.empty_codes);
    widget.select('points');
    data.codes=[{status:'ISSUED',code:'BACKGROUND-READY',expires_at:null}];
    widget.select('codes');await flush();
    expect(widget.body.textContent).toContain('BACKGROUND-READY');
    expect(fetcher.mock.calls.filter(([url])=>url.endsWith('/redemptions'))).toHaveLength(3);
    expect(fetcher.mock.calls.every(([,options])=>options.method==='GET')).toBe(true);
  });
  it('ignores older refresh responses that finish after a more recent balance read', async () => {
    const widget=make();widget.open();await flush();
    let release;
    fetcher.mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));
    const stale=widget.refresh();
    data.me.available=180;
    await widget.refresh();
    release({ok:true,status:200,json:async()=>({...data.me,available:380})});
    await stale;
    expect(widget.me.available).toBe(180);
  });
  it('locks mobile scroll, restores it on close and editor removal', async () => {
    document.body.style.overflow='auto';mobile.matches=true;const widget=make();widget.open();await flush();expect(document.body.style.overflow).toBe('hidden');
    widget.destroy();expect(document.body.style.overflow).toBe('auto');expect(mobile.removeEventListener).toHaveBeenCalled();
  });
  it('inline panel is a region and does not trap or lock the document', async () => {
    mobile.matches=true;const widget=make({mode:'inline'});widget.open();await flush();
    expect(widget.panel.getAttribute('role')).toBe('region');expect(widget.panel.hasAttribute('aria-modal')).toBe(false);expect(widget.locked).toBeUndefined();
  });
  it('requires confirmation, disables it immediately, and renders success safely', async () => {
    const widget=make();widget.open();await flush();widget.select('rewards');
    [...widget.body.querySelectorAll('button')].find(button=>!button.disabled).click();
    expect(fetcher.mock.calls.filter(call=>call[1].method==='POST')).toHaveLength(0);
    const confirm=[...widget.body.querySelectorAll('button')].find(button=>button.textContent===en.lp.confirm);confirm.click();
    expect([...widget.body.querySelectorAll('button')].find(button=>button.textContent===en.lp.redeeming).disabled).toBe(true);
    await flush();expect(widget.body.textContent).toContain('LOCAL-ONLY');
    expect(widget.body.querySelector('a[href*="/discount/"]').getAttribute('href')).toBe('/vi/discount/LOCAL-ONLY?redirect=%2Fvi%2Fcart');
    expect(fetcher.mock.calls.filter(call=>call[1].method==='POST')).toHaveLength(1);
  });
  it('copies with clipboard and fallback, announcing success', async () => {
    const widget=make(), node=document.createElement('button');widget.panel.append(node);
    Object.defineProperty(navigator,'clipboard',{value:{writeText:vi.fn().mockResolvedValue()},configurable:true});
    await widget.copy('LOCAL-ONLY',node);expect(widget.live.textContent).toBe(en.lp.copied);
    Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});document.execCommand=vi.fn().mockReturnValue(true);
    await widget.copy('LOCAL-ONLY',node);expect(document.execCommand).toHaveBeenCalledWith('copy');expect(widget.panel.querySelector('textarea')).toBeNull();
  });
  it('keeps expired codes uncopyable and history deltas signed', async () => {
    data.codes=[{status:'ISSUED',code:'OLD',expires_at:'2000-01-01T00:00:00Z'}];
    const widget=make();widget.open();await flush();widget.select('codes');expect(widget.body.textContent).toContain(en.lp.status_EXPIRED);expect(widget.body.querySelector('button')).toBeNull();
    widget.select('history');expect(widget.body.textContent).toContain('+120');expect(widget.body.textContent).toContain('-200');
  });
});
describe('product and editor lifecycle', () => {
  it('shows the product benefit card with emphasized points and localized explanation', async () => {
    const widget=mountEstimate();await flush();
    expect(widget.card.hidden).toBe(false);
    expect(widget.heading.textContent).toBe(en.lp.estimate_heading);
    expect(widget.help.textContent).toBe(en.lp.estimate_help);
    expect(widget.text.querySelector('strong').textContent).toBe('1');
    document.dispatchEvent(new CustomEvent('variant:change',{detail:{variant:{id:2}}}));
    expect(widget.text.querySelector('strong').textContent).toBe('2');
    expect(widget.text.getAttribute('aria-live')).toBe('polite');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('applies merchant appearance safely, including zero spacing and readable custom text', () => {
    const widget=mountEstimate({designMode:true,appearance:{style:'pill',icon:'none',showHelp:false,
      heading:'<img src=x onerror=alert(1)>',helpText:'<script>alert(1)</script>',fontSize:18,
      marginTop:0,marginBottom:0,radius:0,customColors:true,background:'#ffffff',foreground:'#ffffff'}});
    expect(widget.host.dataset.estimateStyle).toBe('pill');
    expect(widget.root.querySelector('.estimate-icon')).toBeNull();
    expect(widget.heading.textContent).toContain('<img');expect(widget.help.textContent).toContain('<script>');
    expect(widget.root.querySelector('img,script')).toBeNull();expect(widget.help.hidden).toBe(true);
    expect(widget.host.style.getPropertyValue('--lp-estimate-font')).toBe('18px');
    expect(widget.host.style.getPropertyValue('--lp-estimate-top')).toBe('0px');
    expect(widget.host.style.getPropertyValue('--lp-estimate-radius')).toBe('0px');
    expect(widget.host.style.getPropertyValue('--lp-estimate-text')).toBe('#000000');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('hides the entire decorated block for ineligible price, currency or disabled program', async () => {
    data.program.enabled=false;
    const disabled=mountEstimate();await flush();expect(disabled.card.hidden).toBe(true);
    const mismatch=mountEstimate({designMode:true,currency:'EUR'});mismatch.program.currency='USD';mismatch.render();
    expect(mismatch.card.hidden).toBe(true);
    const zero=mountEstimate({designMode:true,variantId:3});expect(zero.card.hidden).toBe(true);
  });
  it('estimates exact floor, updates variant events, ignores other products, hides zero', async () => {
    const widget=mountEstimate();await flush();expect(widget.text.textContent).toContain('1');
    document.dispatchEvent(new CustomEvent('variant:change',{detail:{variant:{id:2}}}));expect(widget.text.textContent).toContain('2');
    document.dispatchEvent(new CustomEvent('variant:change',{detail:{variant:{id:999}}}));expect(widget.text.hidden).toBe(false);
    document.dispatchEvent(new CustomEvent('variant:change',{detail:{variant:{id:3}}}));expect(widget.text.hidden).toBe(true);
  });
  it('shares public program across blocks and hides different presentment currency', async () => {
    const first=mountEstimate();const node=host();node.dataset.proxy=first.host.dataset.proxy;
    const second=new PointsEstimate(node,{...first.config,currency:'EUR'});widgets.push(second);await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);expect(second.text.hidden).toBe(true);
  });
  it('does not request program for editor estimate', () => {
    const widget=mountEstimate({designMode:true});expect(widget.text.hidden).toBe(false);expect(fetcher).not.toHaveBeenCalled();
  });
  it('initializes custom element once, cleans up and reconnects after section reload', () => {
    const node=document.createElement('lp-loyalty');node.dataset.proxy='/editor/apps/loyalty';node.dataset.css='/assets/loyalty-widget.css';
    const config=document.createElement('script');config.type='application/json';config.textContent=JSON.stringify(configuration({designMode:true,previewData:data}));node.append(config);document.body.append(node);
    const first=node.instance;node.connectedCallback();expect(node.instance).toBe(first);expect(node.shadowRoot.querySelectorAll('.launcher')).toHaveLength(1);
    node.remove();expect(first.destroyed).toBe(true);document.body.append(node);expect(node.instance).not.toBe(first);expect(node.shadowRoot.querySelectorAll('.launcher')).toHaveLength(1);
  });
});
