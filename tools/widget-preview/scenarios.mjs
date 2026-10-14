export const rejectionCodes = ['INSUFFICIENT_POINTS','DAILY_LIMIT','UNUSED_CODES_LIMIT','STOCK_EXHAUSTED','CUSTOMER_LIMIT','REWARD_INACTIVE','PROGRAM_DISABLED','CUSTOMER_BLOCKED','UNSUPPORTED_REWARD','KEY_REUSED','ISSUE_FAILED','CUSTOMER_NOT_FOUND','UNKNOWN'];
export const scenarios = ['has-points','membership-tier','tier-grace','birthday-referral','logged-out','new-customer','near-expiry','code-statuses','cart-ready','debt','many-rewards','redeem-success','redeem-pending','pending-timeout','429','500','slow-network','program-disabled', ...rejectionCodes];
export function fixture(name='has-points') {
  const now=Date.now(), iso=days=>new Date(now+days*86400000).toISOString();
  const me={available:380,pending:120,debt:0,expiring_points:0,earliest_expiry:null,unused_issued_codes:0,
    ledger:[{type:'EARN',delta:120,date:iso(-1),reference:'sample-a'},{type:'REDEEM',delta:-200,date:iso(-2),reference:'sample-b'},{type:'REDEEM_REFUND',delta:50,date:iso(-3),reference:'sample-c'}]};
  if(name==='new-customer')Object.assign(me,{available:0,pending:0,ledger:[]});
  if(name==='near-expiry')Object.assign(me,{expiring_points:80,earliest_expiry:iso(3)});
  if(name==='debt')Object.assign(me,{available:0,pending:0,debt:75});
  if(name==='membership-tier')me.tier={enabled:true,name:'Silver',multiplier:'1.25',qualifying_points:380,points_to_next_tier:620,next_tier:'Gold',progress_points:280,progress_target:900};
  if(name==='tier-grace')me.tier={enabled:true,name:'Gold',multiplier:'1.50',qualifying_points:80,
    next_tier:null,points_to_next_tier:null,progress_points:0,progress_target:0,
    grace:{active:true,deadline:iso(7),points_to_keep:20}};
  const rewards=[{id:1,name:'A little thank you',description:'Something lovely for your next order.',points_cost:500,discount_type:'FIXED_AMOUNT',discount_value:'5.00',min_order_amount:'30.00'},
    {id:2,name:'A little extra',description:'Make your next visit even sweeter.',points_cost:200,discount_type:'PERCENTAGE',discount_value:'10.00',min_order_amount:null}];
  if(name==='many-rewards')for(let i=3;i<16;i++)rewards.push({...rewards[0],id:i,name:`A special treat ${i}`,points_cost:i*100});
  rewards.forEach(row=>row.affordable=me.available>=row.points_cost);
  const codes=name==='near-expiry'?[{reference:'older',reward_id:1,points_spent:500,status:'ISSUED',code:'PREVIEW-SAMPLE',created_at:iso(-1),expires_at:iso(3)}]:[];
  if(name==='cart-ready')codes.push(...[1,7].map((days,index)=>({reference:`cart-${index}`,reward_id:index+1,points_spent:200,status:'ISSUED',code:`PREVIEW-CART-${index+1}`,created_at:iso(-1),expires_at:iso(days)})));
  if(name==='code-statuses')codes.push(...['ISSUED','USED','EXPIRED','CANCELLED'].map((status,index)=>({
    reference:`status-${index}`,reward_id:1,points_spent:500,status,
    ...(status==='ISSUED'?{code:'PREVIEW-READY'}:{}),created_at:iso(-5),expires_at:iso(status==='EXPIRED'?-1:3),
  })));
  me.unused_issued_codes=codes.filter(row=>row.status==='ISSUED').length;
  const engagement=name==='birthday-referral';
  if(engagement)me.birthday={month:null,day:null,editable:true,editable_at:null};
  const referral=engagement?{code:'H7K9M2NP',share_link:'https://preview-shop.myshopify.com/?ref=H7K9M2NP',counts:{pending:1,rewarded:2,rejected:0},points_earned:100}:null;
  return {program:{enabled:name!=='program-disabled',currency:'USD',spend_per_point:'10.00',include_tax:false,include_shipping:false,pending_days:7,expiry_days:30,birthday_enabled:engagement,birthday_points:50,referral_enabled:engagement,referral_referrer_points:50,referral_referee_points:20,referral_min_order_amount:'10.00',referral_max_per_referrer_per_month:10},me,rewards,codes,referral};
}
