import '@shopify/ui-extensions/preact';
import type {Api} from '@shopify/ui-extensions/customer-account.page.render';
import {render} from 'preact';
import {useEffect,useMemo,useRef,useState} from 'preact/hooks';
import {createCustomerAPI,RedeemFlow,friendlyError,points,money,date,expiry} from '../../../customer-shared/runtime.js';
import {BACKEND_ORIGIN} from '../../../customer-shared/config.js';
declare const shopify:Api;

export function RewardsPage({api}:{api:Api}) {
  const [model,setModel]=useState<any>(null),[error,setError]=useState<any>(null),[,redraw]=useState(0);
  const [loading,setLoading]=useState(true),[moreBusy,setMoreBusy]=useState(false),[moreError,setMoreError]=useState(false);
  const [revealed,setRevealed]=useState<Record<string,boolean>>({}),[announcement,setAnnouncement]=useState('');
  const controller=useRef(new AbortController()),revision=useRef(0),refreshRef=useRef<()=>Promise<void>>(async()=>{});
  const client=useMemo(()=>createCustomerAPI(BACKEND_ORIGIN,api.sessionToken),[api]);
  const [flow]=useState(()=>new RedeemFlow(client,()=>redraw(value=>value+1),async()=>{
    try {await refreshRef.current();}catch {/* Preserve issued result; show refresh error separately. */}
  }));
  const t=(key:string,values?:Record<string,string|number>)=>String(api.i18n.translate(key,values));
  const n=(value:number)=>points(value,undefined),d=(value:string)=>date(value,undefined);
  const cash=(value:string)=>money(value,model?.program.currency||'USD',undefined);
  const errors=Object.fromEntries(['error_points','error_daily','error_unused','error_stock','error_customer_limit',
    'error_inactive','disabled','error_blocked','error_unsupported','error_key','error_issue','error_new','error_rate',
    'offline','error_network','error_generic'].map(key=>[key,t(key)]));
  refreshRef.current=async()=>{
    const current=++revision.current;
    try {
      const {data:program}=await client('program',{signal:controller.current.signal});
      let data:any={program,me:{},rewards:[],codes:[],history:{results:[],page:1,pages:1}};
      if(program.enabled) {
        const [me,rewards,codes,history]=await Promise.all(['me','rewards','redemptions','history'].map(endpoint=>client(endpoint,{signal:controller.current.signal})));
        data={program,me:me.data,rewards:rewards.data.results,codes:codes.data.results,history:history.data};
      }
      if(current===revision.current&&!controller.current.signal.aborted){setModel(data);setError(null);setRevealed({});}
    }catch(err){if(current===revision.current&&!controller.current.signal.aborted)setError(err);throw err;}
    finally{if(!controller.current.signal.aborted)setLoading(false);}
  };
  useEffect(()=>{
    void refreshRef.current().catch(()=>{});
    return()=>{controller.current.abort();flow.stop();};
  },[client,flow]);
  const refresh=()=>{setLoading(true);void refreshRef.current().catch(()=>{});};
  const loadMore=async()=>{
    if(moreBusy||!model||model.history.page>=model.history.pages)return;
    setMoreBusy(true);setMoreError(false);const current=revision.current;
    try{
      const {data}=await client(`history?page=${model.history.page+1}`,{signal:controller.current.signal});
      if(current===revision.current)setModel((old:any)=>({...old,history:{...data,
        results:[...new Map([...old.history.results,...data.results].map(row=>[row.reference,row])).values()]}}));
    }catch{if(!controller.current.signal.aborted)setMoreError(true);}
    finally{if(!controller.current.signal.aborted)setMoreBusy(false);}
  };
  const closeModal=()=>{
    if(flow.state==='pending'){flow.stop();flow.state='delayed';redraw(value=>value+1);}
    else if(!flow.busy){flow.cancel();if(flow.state==='success'||flow.state==='delayed'){flow.state='idle';redraw(value=>value+1);}}
  };
  const showCode=(code:string,id:string)=> <s-stack gap="base">
    <s-text>{code}</s-text>
    <s-paragraph>{t('checkout_instructions')}</s-paragraph>
    <s-clipboard-item id={id} text={code} onCopy={()=>setAnnouncement(t('copied'))} onCopyError={()=>setAnnouncement(t('copy_failed'))}/>
    <s-button commandFor={id} command="--copy">{t('copy')}</s-button>
  </s-stack>;
  if(!model)return <s-page heading={t('title')}>
    {loading?<s-skeleton-paragraph/>:<s-banner heading={t('error_title')} tone="warning">
      <s-paragraph>{friendlyError(error||{},errors)}</s-paragraph><s-button onClick={refresh}>{t('retry')}</s-button>
    </s-banner>}
  </s-page>;
  if(!model.program.enabled)return <s-page heading={t('title')}><s-banner heading={t('disabled')}>
    <s-paragraph>{t('disabled_help')}</s-paragraph><s-button onClick={refresh} disabled={loading}>{t('refresh')}</s-button>
  </s-banner></s-page>;
  const rewards=[...model.rewards].sort((a,b)=>Number(b.affordable)-Number(a.affordable)||a.points_cost-b.points_cost);
  const next=rewards.filter(row=>row.points_cost>model.me.available).sort((a,b)=>a.points_cost-b.points_cost)[0];
  const expiryStrings={no_expiry:t('no_expiry'),expired:t('expired'),expires_one:t('expires_one'),expires_days:t('expires_days',{days:'%days%'})};
  return <s-page heading={t('title')}>
    <s-stack gap="large">
      <s-button onClick={refresh} disabled={loading||flow.busy}>{t('refresh')}</s-button>
      {error&&<s-banner tone="warning" heading={t('refresh_failed')}><s-button onClick={refresh}>{t('retry')}</s-button></s-banner>}
      {announcement&&<s-banner heading={announcement}/>}
      <s-section heading={t('balance')}>
        <s-stack gap="base">
          {model.me.tier?.enabled&&model.me.tier.grace?.active&&<s-banner tone="warning" heading={t('tier_grace',{
            tier:String(model.me.tier.name),points:n(model.me.tier.grace.points_to_keep),date:d(model.me.tier.grace.deadline)})}/>}
          <s-heading>{t('available_points',{points:n(model.me.available)})}</s-heading>
          <s-paragraph>{t('pending_points',{points:n(model.me.pending),days:n(model.program.pending_days)})}</s-paragraph>
          {model.me.expiring_points>0&&<s-banner tone="warning" heading={t('expiring',{points:n(model.me.expiring_points),date:d(model.me.earliest_expiry)})}/>}
          {model.me.debt>0&&<s-paragraph>{t('debt')}</s-paragraph>}
          {next?<><s-progress value={model.me.available} max={next.points_cost} accessibilityLabel={t('next_reward')}/>
            <s-paragraph>{t('need_points',{points:n(next.points_cost-model.me.available)})}</s-paragraph></>
            :rewards.length>0&&<s-paragraph>{t('can_redeem')}</s-paragraph>}
        </s-stack>
      </s-section>
      <s-section heading={t('rewards')}>
        <s-stack gap="base">
          {rewards.length===0&&<s-paragraph>{t('empty_rewards')}</s-paragraph>}
          {rewards.map(reward=>{
            const discount=reward.discount_type==='PERCENTAGE'?t('percent_off',{value:reward.discount_value}):t('amount_off',{amount:cash(reward.discount_value)});
            const summary=reward.min_order_amount?t('discount_minimum',{discount,amount:cash(reward.min_order_amount)}):discount;
            return <s-box key={reward.id} padding="base" border="base" borderRadius="base">
              <s-stack gap="base">
                <s-heading>{String(reward.name)}</s-heading>
                {reward.description&&<s-paragraph>{String(reward.description)}</s-paragraph>}
                <s-paragraph>{summary}</s-paragraph><s-badge>{t('cost',{points:n(reward.points_cost)})}</s-badge>
                {!reward.affordable&&<><s-progress value={model.me.available} max={reward.points_cost} accessibilityLabel={t('next_reward')}/>
                  <s-paragraph>{t('need_points',{points:n(Math.max(0,reward.points_cost-model.me.available))})}</s-paragraph></>}
                <s-button variant="primary" disabled={!reward.affordable||flow.busy||loading||Boolean(error)}
                  commandFor="redeem-modal" command="--show" onClick={()=>flow.open(reward,model.codes)}>
                  {reward.affordable?t('redeem'):t('not_enough')}
                </s-button>
              </s-stack>
            </s-box>;
          })}
        </s-stack>
      </s-section>
      <s-modal id="redeem-modal" heading={t(flow.state==='success'?'success_title':flow.state==='pending'?'generating':flow.state==='delayed'?'delayed_title':'confirm_title')}
        onHide={closeModal}>
        <s-stack gap="base">
          {(flow.state==='confirm'||flow.state==='error')&&<>
            <s-paragraph>{t('confirm_help',{points:n(flow.reward?.points_cost||0),reward:String(flow.reward?.name||'')})}</s-paragraph>
            <s-paragraph>{t('validity_unknown')}</s-paragraph>
            {flow.error&&<s-banner tone="warning" heading={friendlyError(flow.error,errors)}/>}
            <s-button variant="primary" disabled={flow.busy} loading={flow.busy} onClick={()=>void flow.confirm()}>{t(flow.state==='error'?'retry':'confirm')}</s-button>
          </>}
          {flow.state==='pending'&&<><s-spinner accessibilityLabel={t('generating')}/><s-paragraph>{t('generating_help')}</s-paragraph></>}
          {flow.state==='delayed'&&<><s-paragraph>{t('delayed_help')}</s-paragraph><s-button onClick={refresh} disabled={loading}>{t('refresh_codes')}</s-button></>}
          {flow.state==='success'&&flow.result?.code&&<>
            {showCode(flow.result.code,'new-code')}
            {flow.result.expires_at&&<s-paragraph>{t('valid_until',{date:d(flow.result.expires_at)})}</s-paragraph>}
          </>}
          <s-button commandFor="redeem-modal" command="--hide" disabled={flow.busy&&flow.state!=='pending'}>{t(flow.state==='confirm'?'cancel':'done')}</s-button>
        </s-stack>
      </s-modal>
      <s-section heading={t('my_codes')}>
        <s-stack gap="base">
          <s-paragraph>{t('recent_codes')}</s-paragraph>
          <s-button onClick={refresh} disabled={loading||flow.busy}>{t('refresh_codes')}</s-button>
          {model.codes.length===0&&<s-paragraph>{t('empty_codes')}</s-paragraph>}
          {model.codes.map((row:any)=>{
            const expired=row.expires_at&&new Date(row.expires_at).getTime()<=Date.now();
            const ready=row.status==='ISSUED'&&!expired;
            return <s-box key={row.reference} padding="base" border="base" borderRadius="base">
              <s-stack gap="base"><s-badge>{t(`status_${expired&&row.status==='ISSUED'?'EXPIRED':row.status}`)}</s-badge>
                <s-paragraph>{expiry(row.expires_at,expiryStrings,undefined)}</s-paragraph>
                {ready&&row.code&&(revealed[row.reference]?showCode(row.code,`code-${row.reference}`):
                  <s-button onClick={()=>setRevealed(old=>({...old,[row.reference]:true}))}>{t('open_code')}</s-button>)}
              </s-stack>
            </s-box>;
          })}
        </s-stack>
      </s-section>
      <s-section heading={t('history')}>
        <s-stack gap="base">
          {model.history.results.length===0&&<s-paragraph>{t('empty_history')}</s-paragraph>}
          {model.history.results.map((row:any)=><s-box key={row.reference} padding="base" border="base">
            <s-stack gap="small"><s-text>{t(['EARN','REVOKE','REDEEM','REDEEM_REFUND','EXPIRE','ADJUST'].includes(row.type)?`ledger_${row.type}`:'ledger_unknown')}</s-text>
              <s-text tone={row.delta<0?'critical':'success'}>{`${row.delta>=0?'+':'−'}${n(Math.abs(row.delta))}`}</s-text>
              <s-text>{d(row.date)}</s-text></s-stack>
          </s-box>)}
          {moreError&&<s-banner heading={t('error_generic')} tone="warning"/>}
          {model.history.page<model.history.pages&&<s-button disabled={moreBusy} loading={moreBusy} onClick={()=>void loadMore()}>{t(moreError?'retry':'load_more')}</s-button>}
        </s-stack>
      </s-section>
    </s-stack>
  </s-page>;
}
export default ()=>render(<RewardsPage api={shopify}/>,document.body);
