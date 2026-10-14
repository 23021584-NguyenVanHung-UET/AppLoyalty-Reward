import {beforeEach,describe,expect,it,vi} from 'vitest';
import {mount,flushPromises} from '@vue/test-utils';
import {createMemoryHistory,createRouter} from 'vue-router';
import {apiRequest} from '../api/client';
import EarningView from '../views/EarningView.vue';
import {button,field,page} from './helpers';
import {programDraft,programPayload,validateProgram} from '../lib/validation';
vi.mock('../api/client',async original=>({...await original<typeof import('../api/client')>(),apiRequest:vi.fn()}));
const request=vi.mocked(apiRequest);
beforeEach(()=>request.mockReset());
async function open(kind:string){
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:pathMatch(.*)*',component:EarningView,props:{kind}}]});
  await router.push(`/${kind}`);await router.isReady();const wrapper=mount(EarningView,{props:{kind},global:{plugins:[router]}});await flushPromises();return wrapper;
}
describe('earning definitions',()=>{
  it.each(['earn-rules','campaigns','tiers'])('loads %s through its paginated shop API',async kind=>{
    request.mockResolvedValue(page([]));const wrapper=await open(kind);
    expect(request.mock.calls[0]?.[0]).toBe(kind);expect(request.mock.calls[0]?.[1]?.query?.page).toBe(1);wrapper.unmount();
  });
  it('validates a tier multiplier then saves typed tier values',async()=>{
    request.mockResolvedValue(page([]));const wrapper=await open('tiers');await button(wrapper,'Create tier').trigger('click');
    await field(wrapper,'Name','Gold');await field(wrapper,'Rank','2');await field(wrapper,'Minimum qualifying points','100');
    await field(wrapper,'Multiplier','0.50');await button(wrapper,'Save').trigger('click');expect(request.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0);
    await field(wrapper,'Multiplier','1.25');await button(wrapper,'Save').trigger('click');await flushPromises();
    expect(request.mock.calls.find(([,o])=>o?.method==='POST')?.[1]?.body).toMatchObject({name:'Gold',rank:2,min_qualifying_points:100,multiplier:'1.25'});wrapper.unmount();
  });
  it('requires explicit confirmation before deactivating a rule',async()=>{
    request.mockResolvedValue(page([{id:1,name:'Double points',multiplier:'2.00',is_active:true,rule_type:'PRODUCT',target_ids:[201]}]));
    const wrapper=await open('earn-rules');await button(wrapper,'Deactivate').trigger('click');await flushPromises();
    expect(request.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0);
    const buttons=wrapper.findAll('s-button').filter(node=>node.text()==='Deactivate');await buttons.at(-1)!.trigger('click');await flushPromises();
    expect(request.mock.calls.some(([path,o])=>path==='earn-rules/1/deactivate'&&o?.method==='POST')).toBe(true);wrapper.unmount();
  });
  it('versioned settings include enabled, rolling months and grace with validated limits',()=>{
    const draft=programDraft(null);expect(draft.tiers_enabled).toBe(false);draft.tiers_enabled=true;draft.tier_window_months='6';draft.tier_downgrade_grace_days='7';
    expect(programPayload(draft)).toMatchObject({tiers_enabled:true,tier_window_months:6,tier_downgrade_grace_days:7});
    draft.tier_window_months='121';expect(validateProgram(draft).tier_window_months).toBeTruthy();
  });
});
