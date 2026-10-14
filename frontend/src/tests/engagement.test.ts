import {beforeEach,describe,it,expect,vi} from 'vitest';
import {mount,flushPromises} from '@vue/test-utils';
import {createRouter,createMemoryHistory} from 'vue-router';
import ReferralsView from '../views/ReferralsView.vue';
import {apiRequest} from '../api/client';
import {programDraft,programPayload,validateProgram} from '../lib/validation';
import {button,field,page} from './helpers';
vi.mock('../api/client',async original=>({...await original<typeof import('../api/client')>(),apiRequest:vi.fn()}));
const request=vi.mocked(apiRequest);
beforeEach(()=>request.mockReset());
describe('engagement admin',()=>{
  it('validates and preserves versioned earning bonus controls',()=>{
    const draft=programDraft(null);expect(draft.welcome_trigger).toBe('FIRST_ORDER');expect(draft.birthday_enabled).toBe(false);
    draft.referral_enabled=true;draft.referral_referrer_points='50';draft.referral_referee_points='25';draft.referral_min_order_amount='10.50';
    expect(programPayload(draft)).toMatchObject({referral_enabled:true,referral_referrer_points:50,referral_referee_points:25,referral_min_order_amount:'10.50'});
    draft.referral_min_order_amount='-1';expect(validateProgram(draft).referral_min_order_amount).toBeTruthy();
    draft.referral_referrer_points='1000001';expect(validateProgram(draft).referral_referrer_points).toBeTruthy();
  });
  it('filters referrals and confirms manual rejection of pending rows only',async()=>{
    request.mockResolvedValue(page([{id:1,referrer_profile_id:7,referee_customer_id:301,referee_order_id:1001,status:'PENDING',reject_reason:'',created_at:'2026-10-13T00:00:00Z'},{id:2,status:'REWARDED',created_at:'2026-10-13T00:00:00Z'}]));
    const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:pathMatch(.*)*',component:ReferralsView}]});await router.push('/referrals');await router.isReady();
    const wrapper=mount(ReferralsView,{global:{plugins:[router]}});await flushPromises();
    expect(wrapper.findAll('s-button').filter(node=>node.text()==='Reject')).toHaveLength(1);
    await field(wrapper,'Referrer profile ID','7');await flushPromises();expect(request.mock.calls.at(-1)?.[1]?.query?.referrer).toBe('7');
    await button(wrapper,'Reject').trigger('click');await flushPromises();expect(request.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false);
    await button(wrapper,'Reject referral').trigger('click');await flushPromises();
    expect(request.mock.calls.some(([path,options])=>path==='referrals/1/reject'&&options?.method==='POST')).toBe(true);wrapper.unmount();
  });
});
