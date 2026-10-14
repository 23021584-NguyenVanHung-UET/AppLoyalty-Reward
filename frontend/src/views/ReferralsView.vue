<script setup lang="ts">
import {apiRequest} from '../api/client';
import type {Page} from '../api/types';
import {useResource} from '../composables/resource';
import {useQuery} from '../composables/query';
import {useConfirmAction} from '../composables/confirm';
import {notify} from '../composables/app';
import SelectField from '../components/SelectField.vue';
import Field from '../components/Field.vue';
import DataTable from '../components/DataTable.vue';
import LoadState from '../components/LoadState.vue';
import ActionDialog from '../components/ActionDialog.vue';
type Row={id:number;referrer_profile_id:number|null;referee_customer_id:number;referee_order_id:number;status:string;reject_reason:string;created_at:string;rewarded_at:string|null};
const reasons=['UNKNOWN_CODE','INACTIVE_CODE','SELF_REFERRAL','EXISTING_CUSTOMER','PROFILE_TOO_OLD','BELOW_MINIMUM','MONTHLY_CAP','ALREADY_REFERRED','REFUNDED','CANCELLED','NO_POINTS_HELD','PROFILE_INELIGIBLE','ORDER_MISMATCH','EXPIRED_PENDING','MANUAL_REJECT'];
const query=useQuery(),confirm=useConfirmAction();
const rows=useResource(signal=>apiRequest<Page<Row>>('referrals',{signal,query:{page:query.page.value,status:query.value('status'),reject_reason:query.value('reject_reason'),referrer:query.value('referrer')}}),[query.signature]);
function reject(row:Row){confirm.ask(`Reject referral #${row.id}?`,'This permanently rejects the pending referral. No referral bonus will be granted. The decision is audited.',async()=>{await apiRequest(`referrals/${row.id}/reject`,{method:'POST',body:{}});notify('Referral rejected.');await rows.reload();},'Reject referral',true);}
</script>
<template>
  <s-page heading="Referrals" inlineSize="large"><s-section>
    <p class="muted">Only new customers qualify. Rewards are granted after order points become available, using the configuration saved at attribution. Refunds before grant reject referrals; refunds after grant do not claw referral points back.</p>
    <div class="filters"><SelectField :model-value="query.value('status')" label="Status" :options="[{value:'',label:'All'},...['PENDING','REWARDED','REJECTED'].map(value=>({value,label:value}))]" @update:model-value="query.update({status:$event})"/>
    <SelectField :model-value="query.value('reject_reason')" label="Reject reason" :options="[{value:'',label:'All'},...reasons.map(value=>({value,label:value.replaceAll('_',' ')}))]" @update:model-value="query.update({reject_reason:$event})"/>
    <Field :model-value="query.value('referrer')" label="Referrer profile ID" @update:model-value="query.update({referrer:$event})"/></div>
    <LoadState :loading="rows.loading.value" :error="rows.error.value" @retry="rows.reload"><DataTable :headers="['Referral','Referrer','New customer','Order','Status','Reason','Created','Action']" :page="rows.data.value" @page="query.update({page:$event},false)">
      <tr v-for="row in rows.data.value?.results" :key="row.id"><td>#{{row.id}}</td><td><RouterLink v-if="row.referrer_profile_id" :to="`/customers/${row.referrer_profile_id}`">#{{row.referrer_profile_id}}</RouterLink><span v-else>—</span></td><td>{{row.referee_customer_id}}</td><td>{{row.referee_order_id}}</td><td>{{row.status}}</td><td>{{row.reject_reason||'—'}}</td><td>{{new Date(row.created_at).toLocaleString()}}</td><td><s-button v-if="row.status==='PENDING'" :disabled.prop="confirm.state.busy" @click="reject(row)">Reject</s-button></td></tr>
    </DataTable></LoadState>
  </s-section><ActionDialog :controller="confirm"/></s-page>
</template>
