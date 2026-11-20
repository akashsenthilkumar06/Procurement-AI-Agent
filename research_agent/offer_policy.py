"""Server-only negotiation policy. Private thresholds are never returned by this tool."""
import math
import re


def chat_offer(text, currency):
    """Recognize an explicit current unit-price proposal, leaving other arguments as chat."""
    amount = r"(?:(?P<currency>" + re.escape(currency) + r"|USD|EUR|GBP|[$€£])\s*)?(?P<price>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\d,])"
    intent = (
        r"(?:\b(?:i|we)\s+(?:(?:can|could|will|would)\s+)?(?:offer|pay|do)|"
        r"\b(?:i\s+am|i['’]m|we\s+are|we['’]re)\s+(?:(?:willing|prepared|ready|happy)\s+to\s+)?"
        r"(?:offer|pay|offering|paying|go\s+(?:for|with|to)|buy\s+(?:at|for))|"
        r"\b(?:i|we)['’]d\s+(?:offer|pay|do)|"
        r"\b(?:i|we)['’]d\s+(?:much\s+)?rather\s+(?:come|go|settle)\s+(?:to|at|for)\s+(?:about\s+)?|"
        r"\b(?:my|our)\s+(?:unit[- ]price\s+|counter)?offer\s*(?:is|of|:)?|"
        r"\b(?:can|could|would)\s+you\s+(?:accept|do|take)|"
        r"\b(?:what|how)\s+about|\blet['’]s\s+(?:do|agree\s+(?:on|at)|settle\s+(?:on|at))|"
        r"^\s*(?:offer|counteroffer)(?:\s+of)?)\s*"
    )
    proposals = []
    for statement in re.split(r"[!?;\n]|\.(?!\d)", text):
        if re.search(r"\b(?:if|unless|hypothetical|suppose|assuming|previously|yesterday|last\s+(?:year|month|time))\b", statement, re.I):
            continue
        matches = list(re.finditer(intent + amount, statement, re.I))
        if not matches:
            bare = re.fullmatch(r"\s*" + amount + r"(?:\s+(?:(?:per|a)\s+(?:unit|chip|component)|each))?\s*", statement, re.I)
            matches = [bare] if bare else []
        for match in matches:
            remainder = statement[match.end():]
            if re.match(r"\s*(?:%|percent\b|million\b|billion\b|more\b|less\b|off\b)", remainder, re.I):
                continue
            if re.match(r"\s*(?:units|chips|components|shares|days|weeks)\b", remainder, re.I):
                continue
            if re.match(r"\s*(?:total|in\s+total|for\s+(?:the\s+)?(?:whole|entire|order|\d))\b", remainder, re.I):
                raise ValueError("Please state the price per unit so the supplier can evaluate your offer.")
            if re.match(r"\s*(?:to|[-–])\s*[$€£]?\s*\d", remainder, re.I):
                raise ValueError("Please propose one unit price, such as ‘I can offer $105 per unit’.")
            denomination = match.group('currency')
            if denomination:
                symbols = {'$': ('USD', 'CAD', 'AUD', 'NZD', 'SGD', 'HKD'), '€': ('EUR',), '£': ('GBP',)}
                accepted = currency in symbols.get(denomination, ()) if denomination in symbols else denomination.upper() == currency
                if not accepted:
                    raise ValueError(f"Please give your unit-price offer in {currency}.")
            proposals.append(float(match.group('price').replace(',', '')))
    prices = set(proposals)
    if len(prices) > 1:
        raise ValueError("Your message includes several offers. Please state one unit price to propose.")
    return next(iter(prices)) if prices else None


def chat_acceptance(text, currency):
    """Recognize unconditional agreement to the currently displayed quote."""
    prefix = r"(?:(?:ok(?:ay)?|yes|alright|great|fine)[,\s]+)?"
    agreement = (
        r"(?:i\s+(?:accept|agree)(?:\s+(?:to|with))?"
        r"(?:\s+(?:(?:your|the|this|current)\s+)?(?:price|offer|deal|quote))?|"
        r"deal|agreed|let['’]s\s+do\s+it)"
    )
    amount = (
        r"(?P<price>(?:(?:" + re.escape(currency) + r"|USD|EUR|GBP|[$€£])\s*)?"
        r"\d+(?:,\d{3})*(?:\.\d+)?(?:\s+(?:(?:per|a)\s+(?:unit|chip|component)|each))?)"
    )
    match = re.fullmatch(prefix + agreement + r"(?:\s*(?:(?:at|for|of)\s+)?" + amount + r")?",
                         text.strip().rstrip(".!").strip(), re.I)
    if not match:
        return None
    price = chat_offer(match.group('price'), currency) if match.group('price') else None
    return {'price':price}


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
