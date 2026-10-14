<script setup lang="ts">
import {computed,ref,shallowRef,watch} from 'vue';
import {apiRequest,asApiError,type ApiError} from '../api/client';
import type {Page} from '../api/types';
import {useResource} from '../composables/resource';
import {useQuery} from '../composables/query';
import {useConfirmAction} from '../composables/confirm';
import {notify} from '../composables/app';
import {toLocalInput} from '../lib/format';
import {integer} from '../lib/validation';
import LoadState from '../components/LoadState.vue';
import DataTable from '../components/DataTable.vue';
import Modal from '../components/Modal.vue';
import Field from '../components/Field.vue';
import CheckField from '../components/CheckField.vue';
import SelectField from '../components/SelectField.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import ActionDialog from '../components/ActionDialog.vue';
type Row={id:number;name:string;multiplier:string;is_active:boolean;rule_type?:string;target_ids?:number[];priority?:number;rank?:number;min_qualifying_points?:number;perks_text?:string;min_eligible_spend?:string|null;audience_tier_ids?:number[];starts_at?:string;ends_at?:string|null};
const props=defineProps<{kind:string}>(),query=useQuery(),confirm=useConfirmAction();
const title=computed(()=>({'earn-rules':'Earning rules',campaigns:'Campaigns',tiers:'Tiers'}[props.kind]??'Earning rules'));
const rows=useResource(signal=>apiRequest<Page<Row>>(props.kind,{signal,query:{page:query.page.value,search:query.value('search')}}),[query.signature,()=>props.kind]);
const open=ref(false),selected=ref<Row|null>(null),busy=ref(false),error=shallowRef<ApiError|null>(null),localError=ref('');
const draft=ref({name:'',multiplier:'1.00',is_active:true,rule_type:'PRODUCT',target_ids:'',priority:'0',rank:'0',min_qualifying_points:'0',perks_text:'',min_eligible_spend:'',audience_tier_ids:'',starts_at:'',ends_at:''});
watch(()=>props.kind,()=>{open.value=false;confirm.cancel();});
function edit(row:Row|null){
  selected.value=row;error.value=null;localError.value='';
  draft.value={name:row?.name??'',multiplier:row?.multiplier??'1.00',is_active:row?.is_active??true,rule_type:row?.rule_type??'PRODUCT',target_ids:row?.target_ids?.join(', ')??'',priority:String(row?.priority??0),rank:String(row?.rank??0),min_qualifying_points:String(row?.min_qualifying_points??0),perks_text:row?.perks_text??'',min_eligible_spend:row?.min_eligible_spend??'',audience_tier_ids:row?.audience_tier_ids?.join(', ')??'',starts_at:toLocalInput(row?.starts_at??new Date().toISOString()),ends_at:toLocalInput(row?.ends_at??null)};
  open.value=true;
}
function ids(value:string){const parts=value.split(',').map(v=>v.trim()).filter(Boolean);if(parts.length>1000||parts.some(v=>!integer(v,1,Number.MAX_SAFE_INTEGER)))throw new Error('IDs must be positive whole numbers separated by commas.');return [...new Set(parts.map(Number))];}
function payload(){
  const d=draft.value;
  if(!d.name.trim()||d.name.length>150)throw new Error('Enter a name with 1 to 150 characters.');
  if(!/^\d+(\.\d{1,2})?$/.test(d.multiplier)||Number(d.multiplier)>100||Number(d.multiplier)<(props.kind==='tiers'?1:.01))throw new Error('Multiplier must be positive, at most 100, with at most two decimals. Tier multipliers start at 1.');
  const common={name:d.name.trim(),multiplier:d.multiplier,is_active:d.is_active};
  if(props.kind==='tiers'){
    if(!integer(d.rank,0,1000000)||!integer(d.min_qualifying_points,0,Number.MAX_SAFE_INTEGER))throw new Error('Rank and qualifying points must be non-negative whole numbers.');
    return {...common,rank:Number(d.rank),min_qualifying_points:Number(d.min_qualifying_points),perks_text:d.perks_text};
  }
  const start=new Date(d.starts_at),end=d.ends_at?new Date(d.ends_at):null;
  if(Number.isNaN(start.valueOf())||(end&&(Number.isNaN(end.valueOf())||end<=start)))throw new Error('Enter a valid start time and an end time later than start.');
  const window={starts_at:start.toISOString(),ends_at:end?.toISOString()??null};
  if(props.kind==='earn-rules'){
    const target_ids=ids(d.target_ids);if(!target_ids.length||!integer(d.priority,-100000,100000))throw new Error('At least one target ID and a valid integer priority are required.');
    return {...common,...window,rule_type:d.rule_type,target_ids,priority:Number(d.priority)};
  }
  if(d.min_eligible_spend&&!/^\d{1,16}(\.\d{1,2})?$/.test(d.min_eligible_spend))throw new Error('Minimum spend must be a non-negative decimal.');
  return {...common,...window,min_eligible_spend:d.min_eligible_spend||null,audience_tier_ids:ids(d.audience_tier_ids)};
}
async function save(){
  if(busy.value)return;localError.value='';error.value=null;let body;
  try{body=payload();}catch(failure){localError.value=(failure as Error).message;return;}
  busy.value=true;
  try{await apiRequest(`${props.kind}${selected.value?`/${selected.value.id}`:''}`,{method:selected.value?'PATCH':'POST',body});open.value=false;notify('Saved.');await rows.reload();}
  catch(failure){error.value=asApiError(failure);}finally{busy.value=false;}
}
function toggle(row:Row){
  const active=!row.is_active;
  confirm.ask(`${active?'Activate':'Deactivate'} ${row.name}?`,'Existing earned orders keep saved multipliers. Tier downgrades run daily after grace.',async()=>{
    await apiRequest(`${props.kind}/${row.id}/${active?'activate':'deactivate'}`,{method:'POST',body:{}});notify('Status updated.');await rows.reload();
  },active?'Activate':'Deactivate',!active);
}
</script>
<template>
  <s-page :heading="title" inlineSize="large">
    <s-button slot="primary-action" variant="primary" @click="edit(null)">Create {{kind==='tiers'?'tier':kind==='campaigns'?'campaign':'rule'}}</s-button>
    <s-section>
      <p class="muted">Highest matching line multiplier, then one campaign, then the tier at processing time. Points are rounded down once at the end.</p>
      <p v-if="kind==='tiers'" class="muted">Enable tiers in a new Program settings version. Qualification counts net order points. Upgrades run after commit; downgrades run daily with grace.</p>
      <Field :model-value="query.value('search')" label="Search by name" @update:model-value="query.update({search:$event})"/>
      <LoadState :loading="rows.loading.value" :error="rows.error.value" @retry="rows.reload">
        <DataTable :headers="['Name','Multiplier','Target / qualification','Status','Actions']" :page="rows.data.value" @page="query.update({page:$event},false)">
          <tr v-for="row in rows.data.value?.results" :key="row.id">
            <td>{{row.name}} <small class="muted">#{{row.id}}</small></td><td>×{{row.multiplier}}</td>
            <td>{{kind==='tiers'?`Rank ${row.rank} · ${row.min_qualifying_points} net points`:kind==='earn-rules'?`${row.rule_type}: ${row.target_ids?.join(', ')}`:`Minimum ${row.min_eligible_spend??'0'} · tiers ${row.audience_tier_ids?.join(', ')||'everyone'}`}}</td>
            <td>{{row.is_active?'Active':'Inactive'}}</td><td><div class="actions"><s-button :disabled.prop="confirm.state.busy" @click="edit(row)">Edit</s-button><s-button :disabled.prop="confirm.state.busy" @click="toggle(row)">{{row.is_active?'Deactivate':'Activate'}}</s-button></div></td>
          </tr>
        </DataTable>
      </LoadState>
    </s-section>
    <Modal :open="open" :title="`${selected?'Edit':'Create'} ${title}`" :busy="busy" @close="open=false">
      <ErrorBanner :error="error"/><p v-if="localError" class="field-error" role="alert">{{localError}}</p>
      <div class="form-stack">
        <Field v-model="draft.name" label="Name" :disabled="busy"/><Field v-model="draft.multiplier" label="Multiplier" help="Up to 100, at most two decimals." :disabled="busy"/>
        <template v-if="kind==='tiers'"><Field v-model="draft.rank" label="Rank" :disabled="busy"/><Field v-model="draft.min_qualifying_points" label="Minimum qualifying points" :disabled="busy"/><Field v-model="draft.perks_text" label="Perks" multiline :disabled="busy"/></template>
        <template v-else>
          <template v-if="kind==='earn-rules'"><SelectField v-model="draft.rule_type" label="Rule type" :options="[{value:'PRODUCT',label:'Product'},{value:'COLLECTION',label:'Collection'}]"/><Field v-model="draft.target_ids" label="Target IDs" help="Comma-separated product or collection IDs." :disabled="busy"/><Field v-model="draft.priority" label="Priority" help="Breaks ties between equal multipliers only." :disabled="busy"/></template>
          <template v-else><Field v-model="draft.min_eligible_spend" label="Minimum eligible spend" help="Shop currency before multipliers. Blank means no minimum." :disabled="busy"/><Field v-model="draft.audience_tier_ids" label="Audience tier IDs" help="Internal tier IDs, comma-separated. Blank means everyone." :disabled="busy"/></template>
          <label>Starts at (local time)<input v-model="draft.starts_at" type="datetime-local" :disabled="busy"/></label><label>Ends at (local time, optional)<input v-model="draft.ends_at" type="datetime-local" :disabled="busy"/></label>
        </template>
        <CheckField v-if="!selected" v-model="draft.is_active" label="Active" :disabled="busy"/>
      </div>
      <template #actions><s-button :disabled.prop="busy" @click="open=false">Cancel</s-button><s-button variant="primary" :disabled.prop="busy" @click="save">{{busy?'Saving...':'Save'}}</s-button></template>
    </Modal><ActionDialog :controller="confirm"/>
  </s-page>
</template>
