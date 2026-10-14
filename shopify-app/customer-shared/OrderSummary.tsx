import {useEffect, useState} from 'preact/hooks';
import {createCustomerAPI, loadOrderSummary, numericOrderId, orderMessage, points, date} from './runtime.js';
import {BACKEND_ORIGIN} from './config.js';

type Runtime = {sessionToken:{get:()=>Promise<string>};i18n:{translate:(key:string,values?:Record<string,string|number>)=>string}};
export function OrderSummary({api, orderGid, rewardsHref}:{api:Runtime;orderGid?:string;rewardsHref?:string}) {
  const [summary,setSummary]=useState<any>(null);
  useEffect(()=>{
    const controller=new AbortController();setSummary(null);
    const client=createCustomerAPI(BACKEND_ORIGIN,api.sessionToken);
    void loadOrderSummary(client,numericOrderId(orderGid),controller.signal,(value:any)=>{
      if(!controller.signal.aborted)setSummary(value);
    }).then(value=>{if(!controller.signal.aborted)setSummary(value);})
      .catch(()=>{if(!controller.signal.aborted)setSummary(null);});
    return ()=>controller.abort();
  },[api,orderGid]);
  const t=(key:string,values?:Record<string,string|number>)=>String(api.i18n.translate(key,values));
  const label=orderMessage(summary,t,(n:number)=>points(n,undefined),(value:string)=>date(value,undefined));
  if(!label)return null;
  return <s-banner heading={t('summary_title')}>
    <s-paragraph>{label}</s-paragraph>
    {rewardsHref&&<s-link href={rewardsHref}>{t('view_rewards')}</s-link>}
  </s-banner>;
}
