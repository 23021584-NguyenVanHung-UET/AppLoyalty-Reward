<script setup lang="ts">
import {computed, onScopeDispose, ref, shallowRef} from 'vue';
import {apiRequest, downloadCSV, asApiError, type ApiError} from '../api/client';
import {useResource} from '../composables/resource';
import LoadState from '../components/LoadState.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import CheckField from '../components/CheckField.vue';
import ConfirmDialog from '../components/ConfirmDialog.vue';

type Summary={totals:Record<string,number>;rows:(Record<string,number>&{period:string})[]};
type Liability={available:number;pending:number;outstanding:number;debt:number;expiring_7:number;expiring_30:number;expiring_60:number};
type Redemptions={issued:number;used:number;conversion_rate:number;rows:{reward_id:number;reward__name:string;status:string;count:number}[]};
type Top={lifetime:{profile_id:number;points:number}[];period:{profile_id:number;points:number}[]};
type Distribution={exists:boolean;rows:{tier_id:number|null;tier__name:string|null;count:number}[]};
const today=new Date().toISOString().slice(0,10);
const initial=new Date();initial.setUTCDate(initial.getUTCDate()-29);
const from=ref(initial.toISOString().slice(0,10)),to=ref(today),group=ref('day');
const applied=ref({from:from.value,to:to.value,group_by:group.value});
const validation=ref('');
const data=useResource(async signal=>{
  const [summary,liability,redemptions,top,tiers]=await Promise.all([
    apiRequest<Summary>('reports/points-summary',{signal,query:applied.value}),
    apiRequest<Liability>('reports/liability',{signal}),
    apiRequest<Redemptions>('reports/redemptions',{signal,query:applied.value}),
    apiRequest<Top>('reports/top-customers',{signal,query:applied.value}),
    apiRequest<Distribution>('reports/tier-distribution',{signal}),
  ]);return {summary,liability,redemptions,top,tiers};
},[applied]);
const metrics=['earned','revoked','redeemed','expired','adjusted','net'];
const chartMax=computed(()=>Math.max(1,...(data.data.value?.summary.rows.map(row=>Math.abs(row.net))??[])));
function apply(){
  if(!from.value||!to.value||from.value>to.value){validation.value='Choose a valid date range (from must be before to).';return;}
  validation.value='';applied.value={from:from.value,to:to.value,group_by:group.value};
}
const includePII=ref(false),confirmKind=ref(''),busy=ref(false),error=shallowRef<ApiError|null>(null);
let controller:AbortController|undefined;
onScopeDispose(()=>controller?.abort());
function requestExport(kind:string){if(busy.value)return;if(includePII.value)confirmKind.value=kind;else void exportFile(kind);}
async function exportFile(kind:string){
  if(busy.value)return;busy.value=true;error.value=null;confirmKind.value='';controller=new AbortController();
  try{
    const blob=await downloadCSV(kind,{from:applied.value.from,to:applied.value.to,include_pii:includePII.value?'1':'0'},controller.signal);
    const url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=`loyalty-${kind}.csv`;document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(failure){if(!controller.signal.aborted)error.value=asApiError(failure);}finally{busy.value=false;}
}
</script>
<template>
  <s-page heading="Reports" inlineSize="large">
    <s-section heading="Activity period">
      <form class="report-filters" @submit.prevent="apply">
        <label>From (UTC)<input v-model="from" type="date" required></label>
        <label>To (UTC)<input v-model="to" type="date" required></label>
        <label>Group by<select v-model="group"><option value="day">Day</option><option value="week">Week</option><option value="month">Month</option></select></label>
        <button type="submit">Apply dates</button>
      </form>
      <p v-if="validation" role="alert" class="field-error">{{validation}}</p>
      <p class="muted">Activity comes from the ledger. Redeemed is net of redemption refunds. Current liability is shown as of now.</p>
    </s-section>
    <LoadState :loading="data.loading.value" :error="data.error.value" @retry="data.reload">
      <template v-if="data.data.value">
        <s-section heading="Points summary">
          <div class="table-scroll"><table><thead><tr><th v-for="metric in metrics" :key="metric">{{metric}}</th></tr></thead><tbody><tr><td v-for="metric in metrics" :key="metric">{{data.data.value.summary.totals[metric]}}</td></tr></tbody></table></div>
          <p v-if="!data.data.value.summary.rows.length">No activity in this period.</p>
          <div v-else class="report-chart"><svg viewBox="0 0 800 150" role="img" aria-label="Net points by period">
            <line x1="0" y1="75" x2="800" y2="75" stroke="currentColor"/>
            <rect v-for="(row,index) in data.data.value.summary.rows" :key="row.period" :x="index*800/data.data.value.summary.rows.length" :y="row.net>=0?75-Math.abs(row.net)/chartMax*70:75" :width="Math.max(.5,800/data.data.value.summary.rows.length-2)" :height="Math.abs(row.net)/chartMax*70" :fill="row.net>=0?'#008060':'#c5280c'"><title>{{row.period.slice(0,10)}}: {{row.net}} net points</title></rect>
          </svg></div>
          <details><summary>Daily / weekly / monthly figures</summary><div class="table-scroll"><table><thead><tr><th>Period (UTC)</th><th v-for="metric in metrics" :key="metric">{{metric}}</th></tr></thead><tbody><tr v-for="row in data.data.value.summary.rows" :key="row.period"><td>{{row.period.slice(0,10)}}</td><td v-for="metric in metrics" :key="metric">{{row[metric]}}</td></tr></tbody></table></div></details>
        </s-section>
        <s-section heading="Current liability (points)"><div class="report-kpis"><article v-for="(value,key) in data.data.value.liability" :key="key"><span>{{String(key).replaceAll('_',' ')}}</span><strong>{{value}}</strong></article></div></s-section>
        <s-section heading="Reward redemptions">
          <p>Issued: {{data.data.value.redemptions.issued}} · Used: {{data.data.value.redemptions.used}} · Issued-to-used: {{(data.data.value.redemptions.conversion_rate*100).toFixed(1)}}%</p>
          <div class="table-scroll"><table><thead><tr><th>Reward</th><th>Status</th><th>Count</th></tr></thead><tbody><tr v-for="row in data.data.value.redemptions.rows" :key="`${row.reward_id}:${row.status}`"><td>{{row.reward__name}}</td><td>{{row.status}}</td><td>{{row.count}}</td></tr></tbody></table></div>
        </s-section>
        <s-section heading="Top customers"><div class="report-kpis"><div v-for="period in (['lifetime','period'] as const)" :key="period"><h3>{{period==='lifetime'?'Lifetime earned':'Earned in period'}}</h3><ol><li v-for="row in data.data.value.top[period]" :key="row.profile_id"><RouterLink :to="`/customers/${row.profile_id}`">Customer #{{row.profile_id}}</RouterLink> · {{row.points}} points</li></ol></div></div></s-section>
        <s-section v-if="data.data.value.tiers.exists" heading="Tier distribution"><div class="report-kpis"><article v-for="row in data.data.value.tiers.rows" :key="row.tier_id??'none'"><span>{{row.tier__name??'No tier'}}</span><strong>{{row.count}}</strong></article></div></s-section>
      </template>
    </LoadState>
    <s-section heading="Export CSV"><p class="muted">Exports use the applied dates above. Excel-compatible UTF-8. Names and email addresses are excluded unless selected.</p>
      <CheckField v-model="includePII" label="Include customer names and email addresses" :disabled="busy"/>
      <ErrorBanner :error="error"/>
      <div class="actions"><s-button v-for="kind in ['customers','ledger','lots','redemptions']" :key="kind" :disabled.prop="busy" @click="requestExport(kind)">Export {{kind}}</s-button></div>
    </s-section>
    <ConfirmDialog :open="!!confirmKind" title="Export personal information?" message="This CSV will contain customer names and email addresses. Store it securely and share it only with authorized people. This export is audited." confirm-label="Export with personal information" :busy="busy" @cancel="confirmKind=''" @confirm="exportFile(confirmKind)"/>
  </s-page>
</template>
<style scoped>
.report-filters,.report-kpis{display:flex;flex-wrap:wrap;gap:16px;align-items:end}.report-filters label{display:grid;gap:6px}.report-filters input,.report-filters select,.report-filters button{padding:9px;border:1px solid #8a8a8a;border-radius:8px;background:white}.report-kpis article{display:grid;gap:8px;min-width:140px;background:#f6f8f7;border:1px solid #e3e5e4;padding:16px;border-radius:10px}.report-kpis strong{font-size:26px}.report-chart{max-width:900px;margin:20px 0}.report-chart svg{display:block;width:100%}th{text-transform:capitalize}
</style>
