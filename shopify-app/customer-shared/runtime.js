import { ProxyError, pause } from '../extensions/loyalty-widget/assets/loyalty-core.js';
export { RedeemFlow, friendlyError, points, money, date, expiry } from '../extensions/loyalty-widget/assets/loyalty-core.js';

function abortable(promise, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException('Cancelled', 'AbortError'));
    signal.addEventListener('abort', abort, {once:true});
    Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    if(signal.aborted)abort();
  });
}
export function createCustomerAPI(origin, sessionToken, fetcher = (...args) => fetch(...args)) {
  const base = new URL(origin);
  if(base.protocol!=='https:' || base.username || base.password || base.pathname!=='/' || base.search || base.hash)throw new Error('Invalid backend origin');
  return async (endpoint, {body,signal} = {}) => {
    if(!/^(?:me|program|rewards|redemptions|offers|redeem|history(?:\?page=[1-9][0-9]*)?|orders\/[1-9][0-9]{0,18}\/points)$/.test(endpoint))throw new Error('Invalid endpoint');
    const controller=new AbortController(), abort=()=>controller.abort();
    signal?.throwIfAborted();signal?.addEventListener('abort',abort,{once:true});
    const timeout=setTimeout(abort,10000);
    try {
      const token=await abortable(sessionToken.get(),controller.signal);
      controller.signal.throwIfAborted();
      const response=await fetcher(`${base.origin}/api/customer/${endpoint}`, {
        method:body?'POST':'GET',credentials:'omit',redirect:'error',cache:'no-store',signal:controller.signal,
        headers:{Accept:'application/json',Authorization:`Bearer ${token}`,...(body?{'Content-Type':'application/json'}:{})},
        ...(body?{body:JSON.stringify(body)}:{})
      });
      let data;
      try {data=await response.json();}catch {throw new ProxyError(response.status,'UNAVAILABLE');}
      if(!response.ok)throw new ProxyError(response.status,typeof data?.code==='string'?data.code:'UNAVAILABLE');
      return {data,status:response.status};
    } catch(error) {
      if(signal?.aborted)throw new DOMException('Cancelled','AbortError');
      if(error instanceof ProxyError)throw error;
      throw new ProxyError(0,'NETWORK');
    } finally {clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
  };
}

export function numericOrderId(gid) {
  return /^gid:\/\/shopify\/Order\/([1-9][0-9]{0,18})$/.exec(String(gid))?.[1] ?? null;
}
export async function loadOrderSummary(api, orderId, signal, changed=(_summary)=>{}) {
  if(!orderId)return null;
  const {data:program}=await api('program',{signal});
  if(!program.enabled)return null;
  // Initial request plus at most three retries, spaced three seconds apart.
  for(let attempt=0;attempt<=3;attempt++) {
    try {
      const {data}=await api(`orders/${orderId}/points`,{signal});
      changed(data.status==='NOT_ELIGIBLE'?null:data);
      if(data.status!=='NOT_YET_PROCESSED'||attempt===3)return data.status==='NOT_ELIGIBLE'?null:data;
    } catch(error) {
      // On thank-you the webhook may not have arrived. Treat 404 as unknown,
      // never invent eligibility or earned points, and retry only within this bound.
      if(error.status!==404)throw error;
      if(attempt===3)return null;
    }
    await pause(3000,signal);
  }
  return null;
}
export function orderMessage(data,t,formatPoints,formatDate) {
  if(!data)return '';
  if(data.status==='NOT_YET_PROCESSED')return t('order_waiting');
  if(data.status==='REVOKED')return t('order_revoked');
  if(data.status==='EARNED_PENDING')return t('order_pending',{points:formatPoints(data.points),date:formatDate(data.available_at)});
  if(data.status==='EARNED_AVAILABLE')return t('order_available',{points:formatPoints(data.points)});
  return '';
}
