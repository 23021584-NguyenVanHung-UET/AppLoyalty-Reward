import '@shopify/ui-extensions/preact';
import type {Api} from '@shopify/ui-extensions/purchase.thank-you.block.render';
import {render} from 'preact';
import {useEffect,useState} from 'preact/hooks';
import {OrderSummary} from '../../../customer-shared/OrderSummary';
declare const shopify:Api;
function ThankYou() {
  const [order,setOrder]=useState(shopify.orderConfirmation.value);
  useEffect(()=>shopify.orderConfirmation.subscribe(setOrder),[]);
  // Checkout exposes no verified account-page URL helper here; don't invent a link.
  return <OrderSummary api={shopify} orderGid={order?.order.id}/>;
}
export default ()=>render(<ThankYou/>,document.body);
