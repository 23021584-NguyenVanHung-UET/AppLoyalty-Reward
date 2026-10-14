import { createProxy, getProgram, RedeemFlow, friendlyError, translate, points, money, date, expiry, estimate, discountLink, captureReferral } from './loyalty-core.js';

const idleBalances = new Map();
let locks = 0, previousOverflow = '';
let activeDialog;
function lock() { if (!locks++) { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; } }
function unlock() { if (locks && !--locks) document.body.style.overflow = previousOverflow; }
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function icon(type = 'gift') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', type === 'star' ? 'm12 3 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3L3 9.6l6.2-.9Z' : 'M3 8h18v4H3zm2 4v9h14v-9M12 8v13M12 8H8a3 3 0 1 1 3-3zm0 0h4a3 3 0 1 0-3-3z');
  path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'currentColor'); path.setAttribute('stroke-width', '1.6');
  path.setAttribute('stroke-linejoin', 'round'); svg.append(path); return svg;
}
function button(text, action, className = 'button') {
  const node = el('button', className, text); node.type = 'button'; node.addEventListener('click', action); return node;
}
function readConfig(host) {
  const source = host.querySelector('script[type="application/json"]')?.textContent ?? '{}';
  // Shopify wraps rendered app snippets in HTML comments, even inside JSON scripts.
  // Match JSON strings first so comment-like text inside translations stays intact.
  const json = source.replace(/"(?:\\[\s\S]|[^"\\])*"|<!--[\s\S]*?-->/g,
    token => token.startsWith('<!--') ? '' : token);
  return JSON.parse(json);
}
function styleLink(host, shadow) {
  const link = el('link'); link.rel = 'stylesheet'; link.href = host.dataset.css; shadow.append(link);
}
function contrastColor(primary, requested) {
  const luminance = hex => {
    const channels = hex.match(/[a-f\d]{2}/gi).map(value => parseInt(value, 16) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
  };
  const a = luminance(primary), b = luminance(requested);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05) >= 4.5 ? requested : a > .179 ? '#000000' : '#ffffff';
}
export class LoyaltyWidget {
  constructor(host, config) {
    this.host = host; this.config = config; this.s = config.strings; this.locale = config.locale || 'en';
    this.preview = config.designMode || Boolean(window.Shopify?.designMode); this.floating = config.mode !== 'inline';
    if(!this.preview)void captureReferral(config.rootUrl||window.Shopify?.routes?.root||'/');
    this.root = host.shadowRoot ?? host.attachShadow({ mode: 'open' }); this.root.replaceChildren(); styleLink(host, this.root);
    this.api = createProxy(host.dataset.proxy); this.tab = 'points'; this.opened = false; this.loaded = false;
    this.loggedIn = config.loggedIn; this.flow = new RedeemFlow(this.api, () => this.render(), () => this.refresh());
    this.destroyed = false; this.error = null; this.applySettings(); this.build();
    if (this.preview) this.sample();
    if (config.showPoints && !this.preview && this.loggedIn) {
      const loadBadge = () => {
        if (this.destroyed || this.loaded || this.opened) return;
        if (!idleBalances.has(host.dataset.proxy)) idleBalances.set(host.dataset.proxy, this.api('me').then(r => r.data));
        idleBalances.get(host.dataset.proxy).then(data => {
          if (!this.destroyed && !this.loaded && !this.opened) { this.badge.textContent = this.t('points_short', { points: this.n(data.available) }); this.badge.hidden = false; }
        }).catch(() => {});
      };
      this.idle = window.requestIdleCallback ? window.requestIdleCallback(loadBadge, { timeout: 2000 }) : setTimeout(loadBadge, 1200);
    }
  }
  t(key, values) { return translate(this.s, key, values); }
  n(value) { return points(value, this.locale); }
  cash(value) { return money(value, this.program?.currency || 'USD', this.locale); }
  applySettings() {
    const hex = (value, fallback) => /^#[a-f\d]{6}$/i.test(value) ? value : fallback;
    const primary = hex(this.config.primary, '#5b37d4');
    this.host.style.setProperty('--lp-primary', primary);
    this.host.style.setProperty('--lp-on-primary', contrastColor(primary, hex(this.config.textColor, '#ffffff')));
    this.host.style.setProperty('--lp-radius', `${Math.min(24, Math.max(0, Number(this.config.radius) || 0))}px`);
    this.host.style.setProperty('--lp-offset', `${Math.min(200, Math.max(8, Number(this.config.offset) || 24))}px`);
    this.host.style.setProperty('--lp-z', String(Math.min(9999, Math.max(1, Number(this.config.zIndex) || 1000))));
    this.host.dataset.position = this.config.position === 'bottom-left' ? 'left' : 'right';
    this.host.dataset.mode = this.floating ? 'floating' : 'inline';
  }
  build() {
    this.launcher = button('', () => this.open(), 'launcher'); this.launcher.append(icon(), el('span', '', this.config.label || this.t('launcher')));
    this.badge = el('span', 'launcher-badge'); this.badge.hidden = true; this.launcher.append(this.badge);
    this.launcher.setAttribute('aria-expanded', 'false'); this.launcher.setAttribute('aria-controls','lp-panel');
    if(this.floating)this.launcher.setAttribute('aria-haspopup','dialog');
    this.panel = el('section', `panel ${this.floating ? 'floating' : 'inline'}`); this.panel.hidden = true;
    this.panel.setAttribute('role', this.floating ? 'dialog' : 'region'); this.panel.setAttribute('aria-labelledby', 'lp-title');
    if (this.floating) this.panel.setAttribute('aria-modal', 'true');
    this.panel.tabIndex = -1;this.panel.id='lp-panel';
    const handle = el('div', 'handle'); handle.setAttribute('aria-hidden', 'true');
    const header = el('header', 'header'), title = el('div');
    title.append(el('span', 'eyebrow', this.t('eyebrow'))); const heading = el('h2', '', this.t('title')); heading.id = 'lp-title'; title.append(heading);
    this.closeButton = button('×', () => this.close(), 'icon-button'); this.closeButton.setAttribute('aria-label', this.t('close'));
    header.append(title, this.closeButton); this.panel.append(handle, header);
    this.live = el('div', 'sr-only'); this.live.setAttribute('aria-live', 'polite'); this.live.setAttribute('role', 'status');
    this.tabs = el('div', 'tabs'); this.tabs.setAttribute('role', 'tablist'); this.tabs.setAttribute('aria-label', this.t('sections'));
    this.names = ['points', ...['rewards','codes','history'].filter(name => this.config.tabs?.[name] !== false)];
    this.names.forEach(name => {
      const tab = button(this.t(`tab_${name}`), () => this.select(name), 'tab'); tab.id = `lp-tab-${name}`;
      tab.dataset.tab = name; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'lp-content');
      tab.addEventListener('keydown', event => {
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault(); const index = this.names.indexOf(this.tab);
        const direction = getComputedStyle(this.host).direction === 'rtl' ? -1 : 1;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? this.names.length - 1
          : (index + (event.key === 'ArrowRight' ? direction : -direction) + this.names.length) % this.names.length;
        this.select(this.names[next]); this.tabs.querySelector(`[data-tab="${this.tab}"]`).focus();
      }); this.tabs.append(tab);
    });
    this.body = el('div', 'body'); this.body.id = 'lp-content'; this.body.setAttribute('role', 'tabpanel'); this.body.tabIndex = 0;
    this.panel.append(this.tabs, this.body, this.live);
    this.backdrop = button('', () => this.close(), 'backdrop'); this.backdrop.hidden = true; this.backdrop.tabIndex = -1;
    this.backdrop.setAttribute('aria-label', this.t('close'));
    this.root.append(this.launcher, this.backdrop, this.panel);
    this.keyHandler = event => {
      if (!this.opened) return;
      if (event.key === 'Escape') { event.preventDefault(); this.close(); return; }
      if (event.key !== 'Tab' || !this.floating) return;
      const targets = [...this.panel.querySelectorAll('button:not(:disabled),a[href],[tabindex="0"]')].filter(node => !node.hidden && !node.closest('[hidden]'));
      const first = targets[0], last = targets.at(-1), active = this.root.activeElement;
      if (event.shiftKey && (active === first || active === this.panel)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && active === last) { event.preventDefault(); first?.focus(); }
    };
    this.root.addEventListener('keydown', this.keyHandler);
    this.focusHandler = event => {
      if(activeDialog===this && !event.composedPath().includes(this.host))this.closeButton.focus();
    };
    document.addEventListener('focusin',this.focusHandler);
    this.media = matchMedia('(max-width: 520px)');
    this.resize = () => {
      const needsLock = this.opened && this.floating && this.media.matches;
      if (needsLock && !this.locked) { lock(); this.locked = true; }
      if (!needsLock && this.locked) { unlock(); this.locked = false; }
    };
    this.media.addEventListener('change', this.resize);
    this.render();
  }
  sample() {
    Object.assign(this, this.config.previewData??{program:{enabled:true,currency:'USD'},me:{available:0,pending:0,ledger:[]},rewards:[],codes:[]});
    const iso=days=>new Date(Date.now()+days*86400000).toISOString();
    this.me.earliest_expiry=iso(3);this.me.ledger.forEach((row,i)=>row.date=iso(-i-1));
    this.codes.forEach(row=>row.expires_at=iso(3));
    this.rewards=this.rewards.map(row=>({...row,name:this.t(row.discount_type==='PERCENTAGE'?'sample_percent':'sample_reward'),description:this.t('sample_description')}));
    this.loggedIn = true; this.loaded = true;
  }
  open() {
    if (this.opened || this.destroyed) return;
    if(activeDialog!==this)activeDialog?.close();activeDialog=this.floating?this:null;
    this.opened = true; clearTimeout(this.closeTimer); this.panel.hidden = false; this.backdrop.hidden = !this.floating;
    this.panel.classList.add('is-open'); this.launcher.setAttribute('aria-expanded', 'true'); this.resize();
    this.closeButton.focus(); this.render(); if (!this.loaded) void this.load();
  }
  close() {
    if (!this.opened) return;
    if(activeDialog===this)activeDialog=null;
    this.flow.stop();
    if (this.flow.state === 'pending') this.flow.state = 'delayed';
    this.opened = false; this.panel.classList.remove('is-open'); this.backdrop.hidden = true;
    this.launcher.setAttribute('aria-expanded', 'false'); this.resize(); this.launcher.focus();
    this.closeTimer = setTimeout(() => { if (!this.opened) this.panel.hidden = true; }, 200);
    this.reader?.abort();
  }
  async load() {
    this.reader?.abort(); const reader = this.reader = new AbortController(); this.loading = true; this.error = null; this.render();
    try {
      this.program = await getProgram(this.host.dataset.proxy, this.api);
      if (this.destroyed || reader.signal.aborted || this.reader !== reader) return;
      if (this.program.enabled && this.loggedIn) {
        const signal = reader.signal;
        const endpoints=['me','rewards','redemptions',...(this.program.referral_enabled?['referral']:[])];
        const [me, rewards, codes, referral] = await Promise.all(endpoints.map(name => this.api(name, {signal})));
        if(reader.signal.aborted || this.reader!==reader)return;
        this.me = me.data; this.rewards = rewards.data.results; this.codes = codes.data.results;
        this.referral=referral?.data;
      }
      this.loaded = true;
    } catch (error) {
      if (error.code === 'NOT_LOGGED_IN') { this.loggedIn = false; this.loaded = true; }
      else if (error.name !== 'AbortError') this.error = error;
    } finally { if (!this.destroyed && this.reader===reader) { this.loading = false; this.render(); } }
  }
  async refresh() {
    if (this.preview || this.destroyed) return;
    const revision = this.refreshRevision = (this.refreshRevision || 0) + 1;
    try {
      const endpoints=['me','rewards','redemptions',...(this.program?.referral_enabled?['referral']:[])];
      const [me,rewards,codes,referral] = await Promise.all(endpoints.map(name => this.api(name, {signal: AbortSignal.timeout(10000)})));
      if (this.destroyed || revision !== this.refreshRevision) return;
      this.me = me.data; this.rewards = rewards.data.results; this.codes = codes.data.results;
      this.referral=referral?.data;
      this.refreshFailed = false;
    } catch (error) {
      if (this.destroyed || revision !== this.refreshRevision) return;
      this.refreshFailed = true; if (error.code === 'NOT_LOGGED_IN') this.loggedIn = false;
    }
  }
  select(name, refreshCodes = true) {
    this.tab = name; this.render();
    if (name === 'codes' && refreshCodes && this.loaded && !this.preview) {
      this.live.textContent = this.t('loading');
      void this.refresh().then(() => {
        if (!this.destroyed && this.tab === name) {
          this.render();
          this.live.textContent = this.refreshFailed ? this.t('refresh_failed') : this.t('tab_codes');
        }
      });
    }
  }
  message(key, kind = 'notice') { return el('p', kind, this.t(key)); }
  progress(current, total) {
    const bar = el('progress', 'progress'); bar.max = Math.max(total, 1); bar.value = Math.max(0, Math.min(current, total));
    bar.setAttribute('aria-label', this.t('progress', {points:this.n(current),total:this.n(total)})); return bar;
  }
  summary(reward) {
    const discount = reward.discount_type === 'PERCENTAGE' ? this.t('percent_off', {value:reward.discount_value}) : this.t('amount_off', {value:this.cash(reward.discount_value)});
    return reward.min_order_amount ? this.t('discount_minimum', {discount,minimum:this.cash(reward.min_order_amount)}) : discount;
  }
  render() {
    if (this.destroyed || !this.body) return;
    const focus = this.root.activeElement;
    const restore = focus && this.body.contains(focus);
    this.body.replaceChildren();
    this.tabs.hidden = !this.loggedIn || this.loading || this.error || this.program?.enabled === false || this.flow.state !== 'idle';
    [...this.tabs.children].forEach(tab => { const active = tab.dataset.tab === this.tab; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; });
    this.body.setAttribute('aria-labelledby', this.flow.state === 'idle' ? `lp-tab-${this.tab}` : 'lp-title');
    if (this.preview) this.body.append(el('div', 'preview-ribbon', this.t('preview')));
    if (this.loading) {
      this.live.textContent = this.t('loading'); this.body.setAttribute('aria-busy','true');
      for (let i=0;i<3;i++) { const card = el('div','skeleton'); card.setAttribute('aria-hidden','true'); this.body.append(card); }
    } else {
      this.body.removeAttribute('aria-busy');
      if (this.error) this.renderError();
      else if (this.program?.enabled === false) this.body.append(this.message('disabled'), this.message('disabled_help', 'muted'));
      else if (!this.loggedIn) this.renderLogin();
      else if (this.flow.state !== 'idle') this.renderFlow();
      else if (this.loaded) {
        if (this.refreshFailed) this.body.append(this.message('refresh_failed'), button(this.t('retry'), async () => { await this.refresh(); this.render(); }, 'button secondary'));
        if (this.tab === 'points') this.renderPoints();
        if (this.tab === 'rewards') this.renderRewards();
        if (this.tab === 'codes') this.renderCodes();
        if (this.tab === 'history') this.renderHistory();
      }
    }
    if (this.me && this.config.showPoints) { this.badge.hidden = false; this.badge.textContent = this.t('points_short', {points:this.n(this.me.available)}); }
    if (restore && this.opened) this.body.focus();
  }
  renderError() {
    this.body.append(el('h3','',this.t('error_title')), el('p','muted',friendlyError(this.error,this.s)),button(this.t('retry'),()=>this.load()));
    this.live.textContent = friendlyError(this.error,this.s);
  }
  renderLogin() {
    const card = el('div','welcome'); card.append(icon(),el('h3','',this.t('welcome')),this.message('welcome_help','muted'));
    if (this.program?.spend_per_point) card.append(el('p','rate',this.t('earn_rate',{amount:this.cash(this.program.spend_per_point)})));
    for (const [key,path] of [['login',this.config.loginUrl],['register',this.config.registerUrl]]) {
      const link = el('a',key==='login'?'button':'button secondary',this.t(key));
      const url = new URL(path || '/',location.origin);
      if (url.origin === location.origin) { url.searchParams.set('return_url', location.pathname + location.search); link.href=url.pathname+url.search; card.append(link); }
    }
    this.body.append(card);
  }
  renderPoints() {
    const hero = el('div','hero'); hero.append(el('span','eyebrow',this.t('available')),el('div','balance',this.n(this.me.available)),el('p','muted',this.t('balance_help')));
    const next = [...this.rewards].filter(row=>row.points_cost>this.me.available).sort((a,b)=>a.points_cost-b.points_cost)[0];
    if (next) hero.append(this.progress(this.me.available,next.points_cost),el('p','progress-copy',this.t('next_reward',{points:this.n(next.points_cost-this.me.available)})));
    else if (this.rewards.length) {
      hero.append(this.message('can_redeem','progress-copy'));
      if (this.names.includes('rewards')) hero.append(button(this.t('browse_rewards'),()=>this.select('rewards')));
    }
    const tier=this.me.tier;
    if(tier?.enabled){
      const membership=el('div','membership');
      membership.append(el('span','chip',tier.name||this.t('tier_unassigned')),el('p','small muted',this.t('tier_multiplier',{multiplier:tier.multiplier})));
      if(tier.grace?.active)membership.append(el('p','notice warning tier-grace',this.t('tier_grace',{
        tier:tier.name,points:this.n(tier.grace.points_to_keep),date:date(tier.grace.deadline,this.locale)})));
      if(tier.next_tier&&tier.progress_target>0)membership.append(this.progress(tier.progress_points,tier.progress_target),el('p','small muted',this.t('tier_next',{points:this.n(tier.points_to_next_tier),tier:tier.next_tier})));
      hero.append(membership);
    }
    this.body.append(hero);
    const pending = el('div','stat-row'); pending.append(el('span','',this.t('pending')),el('strong','',this.n(this.me.pending)));
    this.body.append(pending,el('p','muted small',this.program?.pending_days !== undefined ? this.t('pending_help',{days:this.n(this.program.pending_days)}) : this.t('pending_unknown')));
    if (this.me.expiring_points>0) this.body.append(el('p','notice warning',this.t('expiring',{points:this.n(this.me.expiring_points),date:date(this.me.earliest_expiry,this.locale)})));
    if (this.me.debt>0) this.body.append(this.message('debt','notice'));
    if (this.me.unused_issued_codes>0 && this.names.includes('codes')) this.body.append(button(this.t('unused_codes',{count:this.n(this.me.unused_issued_codes)}),()=>this.select('codes'),'button secondary full'));
    if(this.program?.birthday_enabled)this.renderBirthday();
    if(this.program?.referral_enabled)this.renderReferral();
  }
  renderBirthday(){
    const state=this.me.birthday??{month:null,day:null,editable:true},card=el('section','reward engagement');
    card.append(el('h3','',this.t('birthday_title')),el('p','muted small',this.t('birthday_help',{points:this.n(this.program.birthday_points??0)})));
    if(!state.editable){card.append(el('p','',this.t('birthday_saved',{month:state.month,day:state.day})),el('p','small muted',this.t('birthday_unlock',{date:date(state.editable_at,this.locale)})));this.body.append(card);return;}
    this.birthdayDraft??={month:String(state.month??1),day:String(state.day??1),confirm:false};
    const draft=this.birthdayDraft,fields=el('div','engagement-fields'),monthLabel=el('label','',this.t('birthday_month')),dayLabel=el('label','',this.t('birthday_day'));
    const month=el('select'),day=el('select');month.setAttribute('aria-label',this.t('birthday_month'));day.setAttribute('aria-label',this.t('birthday_day'));
    for(let i=1;i<=12;i++){const option=el('option','',new Intl.DateTimeFormat(this.locale,{month:'long',timeZone:'UTC'}).format(new Date(Date.UTC(2000,i-1,1))));option.value=String(i);month.append(option);}month.value=draft.month;
    const days=()=>{day.replaceChildren();const max=new Date(Date.UTC(2000,Number(draft.month),0)).getUTCDate();if(Number(draft.day)>max)draft.day=String(max);for(let i=1;i<=max;i++){const option=el('option','',this.n(i));option.value=String(i);day.append(option);}day.value=draft.day;};days();
    month.addEventListener('change',()=>{draft.month=month.value;days();});day.addEventListener('change',()=>{draft.day=day.value;});month.disabled=day.disabled=!!this.birthdayBusy;
    monthLabel.append(month);dayLabel.append(day);fields.append(monthLabel,dayLabel);card.append(fields);
    const confirmation=el('label','engagement-confirm'),checkbox=el('input');checkbox.type='checkbox';checkbox.checked=draft.confirm;checkbox.disabled=!!this.birthdayBusy;
    const save=button(this.t(this.birthdayBusy?'saving':'birthday_save'),()=>this.saveBirthday());save.disabled=!draft.confirm||!!this.birthdayBusy;
    checkbox.addEventListener('change',()=>{draft.confirm=checkbox.checked;save.disabled=!draft.confirm||!!this.birthdayBusy;});confirmation.append(checkbox,el('span','small',this.t('birthday_confirm')));card.append(confirmation,save);
    if(this.birthdayError)card.append(el('p','notice danger',friendlyError(this.birthdayError,this.s)));
    this.body.append(card);
  }
  async saveBirthday(){
    if(this.birthdayBusy||!this.birthdayDraft?.confirm)return;this.birthdayBusy=true;this.birthdayError=null;this.render();
    try{
      const body={month:Number(this.birthdayDraft.month),day:Number(this.birthdayDraft.day)};
      const result=this.preview?{data:{...body,editable:false,editable_at:new Date(Date.now()+366*86400000).toISOString()}}:await this.api('profile/birthday',{body,signal:AbortSignal.timeout(10000)});
      if(this.destroyed)return;this.me.birthday=result.data;this.birthdayDraft=null;this.live.textContent=this.t('birthday_success');
    }catch(error){this.birthdayError=error;if(error.code==='NOT_LOGGED_IN')this.loggedIn=false;}
    finally{this.birthdayBusy=false;if(!this.destroyed)this.render();}
  }
  renderReferral(){
    const card=el('section','reward engagement');card.append(el('h3','',this.t('referral_title')),el('p','muted',this.t('referral_help',{referrer:this.n(this.program.referral_referrer_points??0),referee:this.n(this.program.referral_referee_points??0)})));
    if(this.program.referral_min_order_amount)card.append(el('p','small muted',this.t('referral_minimum',{amount:this.cash(this.program.referral_min_order_amount)})));
    card.append(el('p','small muted',this.t('referral_limit',{count:this.n(this.program.referral_max_per_referrer_per_month??0)})));
    if(this.referral?.share_link){
      const link=el('input','referral-link');link.readOnly=true;link.value=this.referral.share_link;link.setAttribute('aria-label',this.t('referral_link'));card.append(link);
      const copy=button(this.t('referral_copy'),()=>this.copy(this.referral.share_link,copy),'button secondary');card.append(copy);
    }else{const create=button(this.t(this.referralBusy?'saving':'referral_create'),()=>this.createReferral());create.disabled=!!this.referralBusy;card.append(create);}
    const counts=this.referral?.counts;
    if(counts)card.append(el('p','small muted',this.t('referral_counts',{pending:this.n(counts.pending??0),rewarded:this.n(counts.rewarded??0),points:this.n(this.referral.points_earned??0)})));
    if(this.referralError)card.append(el('p','notice danger',friendlyError(this.referralError,this.s)));
    this.body.append(card);
  }
  async createReferral(){
    if(this.referralBusy)return;this.referralBusy=true;this.referralError=null;this.render();
    try{
      const result=this.preview?{data:{code:'H7K9M2NP',share_link:'https://preview-shop.myshopify.com/?ref=H7K9M2NP',counts:{pending:1,rewarded:2,rejected:0},points_earned:100}}:await this.api('referral/code',{body:{},signal:AbortSignal.timeout(10000)});
      if(!this.destroyed)this.referral=result.data;
    }catch(error){this.referralError=error;if(error.code==='NOT_LOGGED_IN')this.loggedIn=false;}
    finally{this.referralBusy=false;if(!this.destroyed)this.render();}
  }
  renderRewards() {
    if (!this.rewards.length) { this.body.append(this.message('empty_rewards')); return; }
    [...this.rewards].sort((a,b)=>Number(b.affordable)-Number(a.affordable)||a.points_cost-b.points_cost).forEach(reward=>{
      const card=el('article','reward'); const top=el('div','reward-top');
      top.append(el('span','discount',this.summary(reward)),el('span','chip',this.t('points_short',{points:this.n(reward.points_cost)})));
      card.append(top,el('h3','',reward.name),el('p','muted',reward.description));
      const missing=Math.max(0,reward.points_cost-this.me.available), unavailable=reward.is_active===false;
      if(missing) card.append(this.progress(this.me.available,reward.points_cost),el('p','small muted',this.t('need_points',{points:this.n(missing)})));
      const action=button(this.t(unavailable?'unavailable':missing||this.me.debt>0?'not_enough':'redeem'),()=>{
        if(this.preview) {this.live.textContent=this.t('preview_no_redeem');this.body.append(this.message('preview_no_redeem'));return;}
        this.flow.open(reward,this.codes);
      },'button full');
      action.disabled=Boolean(missing||this.me.debt>0||unavailable||this.refreshFailed); card.append(action); this.body.append(card);
    });
  }
  renderFlow() {
    const flow=this.flow, reward=flow.reward;
    if (flow.state==='confirm'||flow.state==='error') {
      this.body.append(el('h3','',this.t('confirm_title',{points:this.n(reward.points_cost),discount:this.summary(reward)})),this.message('confirm_help','muted'));
      if(flow.error) {this.body.append(el('p','notice danger',friendlyError(flow.error,this.s)));this.live.textContent=friendlyError(flow.error,this.s);}
      const actions=el('div','actions'); const cancel=button(this.t('cancel'),()=>flow.cancel(),'button secondary'); cancel.disabled=flow.busy;
      const confirm=button(this.t(flow.busy?'redeeming':flow.state==='error'?'retry':'confirm'),()=>flow.confirm());confirm.disabled=flow.busy;
      actions.append(cancel,confirm);this.body.append(actions);
    } else if(flow.state==='success') {
      this.body.append(el('div','success-mark','✓'),el('h3','',this.t('success_title')),this.message('success_help','muted'));
      this.codeView(flow.result.code,flow.result.expires_at,true);
      if(this.names.includes('codes'))this.body.append(button(this.t('view_codes'),()=>{flow.state='idle';this.select('codes');},'button secondary full'));
      else this.body.append(button(this.t('done'),()=>{flow.state='idle';this.render();},'button secondary'));
      this.live.textContent=this.t('success_title');
    } else {
      this.body.append(el('div','pending-mark','◷'),el('h3','',this.t(flow.state==='delayed'?'delayed_title':'generating')),this.message(flow.state==='delayed'?'delayed_help':'generating_help','muted'));
      this.live.textContent=this.t(flow.state==='delayed'?'delayed_help':'generating');
      if(flow.state==='delayed')this.body.append(button(this.t(this.names.includes('codes')?'view_codes':'done'),async()=>{await this.refresh();flow.state='idle';this.select(this.names.includes('codes')?'codes':'points',false);}));
    }
  }
  codeView(code,expiresAt,apply=false) {
    const box=el('div','code-box');box.append(el('code','code',code));
    const copy=button(this.t('copy'),()=>this.copy(code,copy),'button secondary');box.append(copy);
    if(expiresAt)box.append(el('p','small muted',this.t('valid_until',{date:date(expiresAt,this.locale)})));
    if(apply&&!this.preview){const link=el('a','button full',this.t('apply'));link.href=discountLink(code,this.config.rootUrl);box.append(link);}
    this.body.append(box);
  }
  async copy(code,copyButton) {
    try {
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(code);
      else throw new Error('Fallback');
    } catch {
      const area=el('textarea','copy-area');area.value=code;this.panel.append(area);area.focus();area.select();
      const copied=Boolean(document.execCommand?.('copy'));area.remove();copyButton.focus();
      if(!copied){this.live.textContent=this.t('copy_failed');return;}
    }
    copyButton.textContent=this.t('copied');this.live.textContent=this.t('copied');
  }
  renderCodes() {
    if(!this.codes?.length){this.body.append(this.message('empty_codes'));return;}
    this.codes.forEach(row=>{
      const status=row.status==='ISSUED'&&row.expires_at&&new Date(row.expires_at).getTime()<=Date.now()?'EXPIRED':row.status;
      const card=el('article','reward');card.append(el('span',`chip ${status==='ISSUED'?'ready':''}`,this.t(`status_${status}`)));
      if(row.code){card.append(el('code','code small-code',row.code));if(status==='ISSUED')card.append(button(this.t('copy'),event=>this.copy(row.code,event.currentTarget),'button secondary'));}
      card.append(el('p','small muted',expiry(row.expires_at,this.s,this.locale)));this.body.append(card);
    });
    if(this.codes.length>=20)this.body.append(this.message('recent_codes','muted small'));
  }
  renderHistory() {
    if(!this.me.ledger?.length){this.body.append(this.message('empty_history'));return;}
    this.me.ledger.forEach(row=>{
      const item=el('div','history-row'),text=el('div');text.append(el('strong','',this.t(`ledger_${row.type}`)||this.t('ledger_unknown')),el('time','small muted',date(row.date,this.locale)));
      const delta=el('span',row.delta<0?'delta negative':'delta positive',`${row.delta>=0?'+':''}${this.n(row.delta)}`);item.append(text,delta);this.body.append(item);
    });
    if(this.me.ledger.length>=20)this.body.append(this.message('recent_history','muted small'));
  }
  destroy() {
    if(activeDialog===this)activeDialog=null;
    this.destroyed=true;this.reader?.abort();this.flow.stop();clearTimeout(this.closeTimer);clearTimeout(this.idle);
    if(window.cancelIdleCallback)window.cancelIdleCallback(this.idle);
    this.root.removeEventListener('keydown',this.keyHandler);this.media.removeEventListener('change',this.resize);document.removeEventListener('focusin',this.focusHandler);
    if(this.locked){unlock();this.locked=false;}
  }
}
export class PointsEstimate {
  constructor(host,config) {
    this.host=host;this.config=config;this.root=host.shadowRoot??host.attachShadow({mode:'open'});this.root.replaceChildren();styleLink(host,this.root);
    const appearance=config.appearance??{},hex=(value,fallback)=>/^#[a-f\d]{6}$/i.test(value)?value:fallback;
    const size=(value,fallback,min,max)=>value===null||value===undefined||!Number.isFinite(Number(value))?fallback:Math.min(max,Math.max(min,Number(value)));
    const accent=hex(appearance.accent,'#5b37d4');
    host.dataset.estimateStyle=['card','pill','text'].includes(appearance.style)?appearance.style:'card';
    host.style.setProperty('--lp-estimate-accent',accent);
    host.style.setProperty('--lp-estimate-on-accent',contrastColor(accent,'#ffffff'));
    for(const [name,value,fallback,min,max] of [['font',appearance.fontSize,15,12,22],['padding',appearance.padding,16,8,24],
      ['radius',appearance.radius,16,0,24],['top',appearance.marginTop,8,0,32],['bottom',appearance.marginBottom,8,0,32]])
      host.style.setProperty(`--lp-estimate-${name}`,`${size(value,fallback,min,max)}px`);
    if(appearance.customColors===true){
      const background=hex(appearance.background,'#f6f2ff'),foreground=contrastColor(background,hex(appearance.foreground,'#2d204e'));
      host.style.setProperty('--lp-estimate-bg',background);host.style.setProperty('--lp-estimate-text',foreground);host.style.setProperty('--lp-estimate-muted',foreground);
    }else for(const name of ['bg','text','muted'])host.style.removeProperty(`--lp-estimate-${name}`);
    this.card=el('div','estimate-card');this.card.hidden=true;this.root.append(this.card);
    if(appearance.icon!=='none'){const badge=el('span','estimate-icon');badge.setAttribute('aria-hidden','true');badge.append(icon(appearance.icon));this.card.append(badge);}
    const copy=el('div','estimate-copy');this.card.append(copy);
    this.heading=el('span','estimate-heading',appearance.heading?.trim()||config.strings.estimate_heading||'');
    this.heading.hidden=!this.heading.textContent;
    this.text=el('p','estimate');this.text.hidden=true;this.text.setAttribute('aria-live','polite');this.text.setAttribute('aria-atomic','true');
    this.help=el('p','estimate-help',appearance.helpText?.trim()||config.strings.estimate_help);
    this.help.hidden=appearance.showHelp===false;copy.append(this.heading,this.text,this.help);
    this.variant=config.variantId;this.destroyed=false;
    this.update=event=>{
      const raw=event?.detail?.variant?.id??event?.detail?.data?.variant?.id;
      if(raw&&!this.config.variants.some(row=>String(row.id)===String(raw)))return;
      const scope=host.closest('section')??document;
      const input=scope.querySelector('form[action*="/cart/add"] [name="id"]');
      this.variant=String(raw??input?.value??new URL(location.href).searchParams.get('variant')??this.variant);
      this.render();
    };
    this.events=['change','variant:change','variant:changed','product:variant-change'];
    this.events.forEach(name=>document.addEventListener(name,this.update));
    this.observer=new MutationObserver(this.update);
    const scope=host.closest('section')??document;
    this.observer.observe(scope,{childList:true,subtree:true,attributes:true,attributeFilter:['value']});
    if(config.designMode||window.Shopify?.designMode){this.program={enabled:true,currency:config.currency,spend_per_point:'10.00'};this.render();}
    else getProgram(host.dataset.proxy,createProxy(host.dataset.proxy)).then(program=>{this.program=program;this.render();}).catch(()=>{});
  }
  render(){
    if(this.destroyed||!this.program)return;
    const variant=this.config.variants.find(row=>String(row.id)===String(this.variant));
    this.card.hidden=this.text.hidden=!this.program.enabled||this.program.currency!==this.config.currency||!variant||BigInt(String(variant.price))<=0n;
    if(!this.text.hidden){
      const count=estimate(variant.price,this.program.spend_per_point);
      const label=translate(this.config.strings,'estimate',{points:points(BigInt(count),this.config.locale)});
      if(this.text.textContent!==label){
        const parts=this.config.strings.estimate.split('%points%');
        if(parts.length===2)this.text.replaceChildren(document.createTextNode(parts[0]),el('strong','estimate-value',points(BigInt(count),this.config.locale)),document.createTextNode(parts[1]));
        else this.text.textContent=label;
      }
      this.text.title=this.help.textContent;
    }
  }
  destroy(){this.destroyed=true;this.observer.disconnect();this.events.forEach(name=>document.removeEventListener(name,this.update));}
}
if(!customElements.get('lp-loyalty'))customElements.define('lp-loyalty',class extends HTMLElement{
  connectedCallback(){if(!this.instance)this.instance=new LoyaltyWidget(this,readConfig(this));}
  disconnectedCallback(){this.instance?.destroy();this.instance=null;}
});
if(!customElements.get('lp-estimate'))customElements.define('lp-estimate',class extends HTMLElement{
  connectedCallback(){if(!this.instance)this.instance=new PointsEstimate(this,readConfig(this));}
  disconnectedCallback(){this.instance?.destroy();this.instance=null;}
});
