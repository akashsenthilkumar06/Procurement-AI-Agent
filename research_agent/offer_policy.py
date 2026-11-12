"""Server-only negotiation policy. Private thresholds are never returned by this tool."""
import math


def evaluate_offer(offer, pack, condition):
    policy=pack['offer_policy']
    if offer is not None and (isinstance(offer,bool) or not isinstance(offer,(int,float)) or not math.isfinite(offer) or abs(offer*100-round(offer*100))>1e-6):
        raise ValueError('Offer must be a finite unit price with at most two decimals')
    private=condition.get('private_information',{})
    lower=condition.get('private_rules',{}).get('min',{}).get('price',private.get('reservation_price',policy['private_min']))
    upper=condition.get('private_rules',{}).get('max',{}).get('price',private.get('buyer_budget',policy['private_max']))
    if offer is not None and not policy['public_min']<=offer<=policy['public_max']:
        return {'decision':'reject','amount':None}
    if offer is not None and lower<=offer<=upper:
        return {'decision':'accept','amount':None}
    # Counter on a public price grid, strictly inside private bounds. Never echo a private limit.
    counter=policy['opening_price'];step=policy['counter_step']
    if counter<=lower:counter=(math.floor(lower/step)+1)*step
    if counter>=upper:counter=(math.ceil(upper/step)-1)*step
    if not lower<counter<upper or not policy['public_min']<=counter<=policy['public_max']:
        return {'decision':'reject','amount':None}
    return {'decision':'counter','amount':round(counter,2)}
