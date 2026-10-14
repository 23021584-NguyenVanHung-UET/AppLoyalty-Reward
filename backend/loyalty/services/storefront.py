"""Customer operations; identity has already been authenticated by Shopify."""
import time

from django.conf import settings
from django.core.cache import cache

from loyalty.models import Redemption
from .errors import LoyaltyError
from .redemption import redeem_reward


def redeem(shop, customer_id, reward_id, client_key):
    window = int(time.time() // 60)
    cache_key = f"loyalty:redeem-rate:{shop.pk}:{customer_id}:{window}"
    if cache.add(cache_key, 1, timeout=65):
        count = 1
    else:
        try:
            count = cache.incr(cache_key)
        except ValueError:
            cache.add(cache_key, 1, timeout=65)
            count = 1
    if count > settings.LOYALTY_PROXY_REDEEM_PER_MINUTE:
        raise LoyaltyError("RATE_LIMITED", "Bạn đổi thưởng quá nhanh. Vui lòng thử lại sau một phút.", 429)
    outcome = redeem_reward(shop, customer_id, reward_id, f"{customer_id}:{client_key}")
    if outcome.status == "REJECTED":
        raise LoyaltyError(outcome.reason_code, outcome.message,
                           422 if outcome.reason_code in ("INVALID_KEY", "INVALID_ID", "UNSUPPORTED_REWARD") else 409)
    if outcome.status == "PENDING":
        return {"status": "PENDING", "message": "Mã sẽ sẵn sàng sau ít phút. Vui lòng kiểm tra lại.",
                "poll": "redemptions", "retry_after_seconds": 10}, 202
    row = Redemption.objects.filter(pk=outcome.redemption_id, profile__shop=shop,
                                    profile__shopify_customer_id=customer_id).first()
    return {"status": "ISSUED", "code": outcome.code,
            "expires_at": row.code_expires_at if row else None}, 200
