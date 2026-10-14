import '@shopify/ui-extensions/preact';
import type {Api} from '@shopify/ui-extensions/purchase.checkout.block.render';
import {render} from 'preact';
import {useEffect,useMemo,useRef,useState} from 'preact/hooks';
import {createCustomerAPI} from '../../../customer-shared/runtime.js';
import {BACKEND_ORIGIN} from '../../../customer-shared/config.js';

declare const shopify:Api;
type Offer={reference:string;code:string;name:string;description:string;discount_type:string;
  discount_value:string;min_order_amount:string|null;currency:string;expires_at:string|null};
function useSignal<T>(signal:{value:T;subscribe:(callback:(value:T)=>void)=>()=>void}|undefined,fallback:T):T {
  const [value,setValue]=useState(signal?.value??fallback);
  useEffect(()=>{setValue(signal?.value??fallback);return signal?.subscribe(setValue);},[signal]);
  return value;
}
export function CheckoutOffers({api}:{api:Api}) {
  const preview=Boolean(api.extension.editor), customer=useSignal(api.buyerIdentity?.customer,undefined);
  const applied=useSignal(api.discountCodes,[]),instructions=useSignal(api.instructions,api.instructions.value);
  const currency=useSignal(api.cost.totalAmount,api.cost.totalAmount.value).currencyCode;
  const [offers,setOffers]=useState<Offer[]>([]),[loading,setLoading]=useState(false),[failed,setFailed]=useState(false);
  const [offersIdentity,setOffersIdentity]=useState<string|null>(null);
  const [needsLogin,setNeedsLogin]=useState(false),[disabled,setDisabled]=useState(false),[busy,setBusy]=useState<string|null>(null);
  const [applyError,setApplyError]=useState(false),[appliedMessage,setAppliedMessage]=useState(false),[tick,setTick]=useState(0);
  const client=useMemo(()=>createCustomerAPI(BACKEND_ORIGIN,api.sessionToken),[api]);
  const generation=useRef(0),lock=useRef(false);
  const t=(key:string,values:Record<string,string|number>={})=>String(api.i18n.translate(key,values))
    // Older locale bundles can return literal {value}; also handle an unresolved
    // Shopify {{value}}. Replace only supplied fields, once, as plain text.
    .replace(/\{\{\s*(\w+)\s*\}\}|\{(\w+)\}/g,(match,double,single)=>{
      const name=double??single;
      return Object.hasOwn(values,name)?String(values[name]):match;
    });
  const identity=customer?.id??null;
  useEffect(()=>{
    const version=++generation.current,controller=new AbortController();
    lock.current=false;setBusy(null);setOffers([]);setFailed(false);setNeedsLogin(false);setApplyError(false);setAppliedMessage(false);setDisabled(false);
    if(preview){
      setOffers([{reference:'sample',code:'PREVIEW-10',name:t('checkout_sample_name'),description:t('checkout_sample_description'),
        discount_type:'PERCENTAGE',discount_value:'10',min_order_amount:'50.00',currency,expires_at:null}]);
      setOffersIdentity(null);setLoading(false);return()=>{generation.current++;};
    }
    if(!identity){setLoading(false);setNeedsLogin(true);return()=>{generation.current++;};}
    setLoading(true);
    void (async()=>{
      try {
        const {data:program}=await client('program',{signal:controller.signal});
        if(controller.signal.aborted||version!==generation.current)return;
        setDisabled(!program.enabled);
        if(program.enabled){
          const {data}=await client('offers',{signal:controller.signal});
          if(!Array.isArray(data.results))throw new Error('Invalid offers');
          if(!controller.signal.aborted&&version===generation.current){setOffers(data.results);setOffersIdentity(identity);}
        }
      }catch(error:any){
        if(!controller.signal.aborted&&version===generation.current){
          if(error.status===401)setNeedsLogin(true);else setFailed(true);
        }
      }finally{if(!controller.signal.aborted&&version===generation.current)setLoading(false);}
    })();
    return()=>{controller.abort();generation.current++;};
  },[client,identity,preview,tick,currency]);
  useEffect(()=>{
    const deadline=Math.min(...offers.filter(row=>row.expires_at).map(row=>Date.parse(row.expires_at!)).filter(value=>Number.isFinite(value)&&value>Date.now()));
    if(Number.isFinite(deadline)){const timer=setTimeout(()=>setTick(value=>value+1),Math.min(2147483647,Math.max(1,deadline-Date.now())));return()=>clearTimeout(timer);}
  },[offers]);
  const valid=(preview||offersIdentity===identity?offers:[]).filter(row=>row.currency===currency&&(!row.expires_at||Date.parse(row.expires_at)>Date.now())).slice(0,5);
  const canApply=instructions.discounts.canUpdateDiscountCodes;
  async function apply(offer:Offer) {
    if(preview||lock.current||!canApply||api.buyerIdentity?.customer.value?.id!==offersIdentity||applied.some(row=>row.code.toUpperCase()===offer.code.toUpperCase())
      ||(offer.expires_at&&Date.parse(offer.expires_at)<=Date.now()))return;
    const version=generation.current;lock.current=true;setBusy(offer.reference);setApplyError(false);setAppliedMessage(false);
    try {
      const result=await api.applyDiscountCodeChange({type:'addDiscountCode',code:offer.code});
      if(version!==generation.current)return;
      if(result.type==='success')setAppliedMessage(true);else setApplyError(true);
    }catch{if(version===generation.current)setApplyError(true);}
    finally{if(version===generation.current){lock.current=false;setBusy(null);}}
  }
  return <s-section heading={t('checkout_offers_title')}><s-stack gap="base">
    {preview&&<s-banner heading={t('checkout_preview')}/>}
    {loading?<s-skeleton-paragraph/>:needsLogin?<s-paragraph>{t('checkout_login')}</s-paragraph>
      :disabled?<s-paragraph>{t('checkout_disabled')}</s-paragraph>
      :failed?<s-banner tone="warning" heading={t('checkout_load_error')}/>
      :<>
        {!valid.length&&<s-paragraph>{t('checkout_empty')}</s-paragraph>}
        {valid.map(offer=>{
          const present=applied.some(row=>row.code.toUpperCase()===offer.code.toUpperCase());
          const cash=(value:string)=>api.i18n.formatCurrency(Number(value),{currency:offer.currency});
          const benefit=offer.discount_type==='PERCENTAGE'?t('checkout_percent',{value:offer.discount_value}):t('checkout_amount',{amount:cash(offer.discount_value)});
          return <s-box key={offer.reference} padding="base" border="base" borderRadius="base"><s-stack gap="base">
            <s-heading>{offer.name||benefit}</s-heading>
            <s-text type="strong">{offer.code}</s-text>
            <s-paragraph>{benefit}</s-paragraph>
            {offer.description&&<s-paragraph>{offer.description}</s-paragraph>}
            {offer.min_order_amount&&<s-paragraph>{t('checkout_minimum',{amount:cash(offer.min_order_amount)})}</s-paragraph>}
            {offer.expires_at&&<s-paragraph>{t('checkout_expiry',{date:api.i18n.formatDate(new Date(offer.expires_at),{dateStyle:'medium'})})}</s-paragraph>}
            <s-button disabled={preview||Boolean(busy)||!canApply||present} loading={busy===offer.reference} onClick={()=>apply(offer)}>
              {t(present?'checkout_applied':'checkout_apply')}
            </s-button>
          </s-stack></s-box>;
        })}
        {valid.length>0&&<s-paragraph>{t('checkout_terms')}</s-paragraph>}
        {!canApply&&valid.length>0&&<s-paragraph>{t('checkout_cannot_apply')}</s-paragraph>}
      </>}
    {applyError&&<s-banner tone="warning" heading={t('checkout_apply_error')}/>}
    {appliedMessage&&<s-banner tone="success" heading={t('checkout_apply_success')}/>}
    {!preview&&!loading&&<s-button variant="secondary" disabled={Boolean(busy)} onClick={()=>setTick(value=>value+1)}>{t('checkout_refresh')}</s-button>}
  </s-stack></s-section>;
}
export default()=>render(<CheckoutOffers api={shopify}/>,document.body);
