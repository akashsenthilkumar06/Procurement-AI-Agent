"""Final server output gate. Private conditions use only deterministic, public-field templates."""
import math
import re

from .config import private_mode
from .providers import ProviderError


def secret_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from secret_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from secret_values(item)
    elif isinstance(value, str) and len(value.strip()) >= 3:
        yield value.strip()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield f"{value:g}"


def check_output(config, condition, response):
    """Fail closed before persistence/delivery; do not rely on a model's promise of secrecy."""
    content = response.get("content")
    if not isinstance(content, str) or not content.strip() or len(content) > 16000 or "\x00" in content:
        raise ProviderError("Response failed the output check")
    if condition["output"] == "number":
        try:
            if not math.isfinite(float(content)):
                raise ValueError()
        except ValueError:
            raise ProviderError("The condition requires a finite numeric answer") from None
    option = response.get("option_id")
    if option not in [None]+[o["id"] for o in config["scenario"]["options"]]:
        raise ProviderError("Response contains an unknown choice")
    if condition["authority"] == "chat" and (option is not None or response.get("offer_price") is not None):
        raise ProviderError("Chatbot responses cannot make a structured decision")
    price = response.get("offer_price")
    if price is not None:
        n = config["negotiation"]
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or not n["price_min"] <= price <= n["price_max"]:
            raise ProviderError("Response price is outside the public range")
    if config.get("task")=="negotiation" and condition["output"]=="number" and not response.get("metadata",{}).get("safe_fallback"):
        value=float(content)
        n=config["negotiation"]
        if not n["price_min"]<=value<=n["price_max"] or price is not None and value!=price:
            raise ProviderError("Numeric reply does not match the approved public offer")
    if private_mode(condition):
        if response.get("metadata", {}).get("response_source") not in ("private_policy_template", "negotiation_private_template"):
            raise ProviderError("Private conditions require an approved server response")
        secrets=secret_atoms([condition['private_information'],condition['private_rules']])
        flags=disclosure_flags(content,secrets)
        if flags:
            response['content']=safe_reply(secrets,condition['output']=='number')
            response['metadata'].update(safe_fallback=True,output_check='replaced',output_flags=flags)
    response["metadata"].setdefault("output_check", "passed")
    return response


SAFE_REPLY='The response was withheld to protect study information. Please review the available study controls.'
WORDS={'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,'eighteen':18,'nineteen':19,'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,'seventy':70,'eighty':80,'ninety':90}
NUMBER_WORD=re.compile(r'\b(?:'+ '|'.join(WORDS)+r'|hundred|thousand|and|point)\b',re.I)


def expand_number_words(text):
    pattern=r'\b(?:'+ '|'.join(WORDS)+r'|hundred|thousand)(?:[ -]+(?:'+ '|'.join(WORDS)+r'|hundred|thousand|and))*\b'
    def number(match):
        total=0;current=0
        for word in re.split(r'[ -]+',match.group().lower()):
            if word in WORDS:current+=WORDS[word]
            elif word=='hundred':current=max(1,current)*100
            elif word=='thousand':total+=max(1,current)*1000;current=0
        return str(total+current)
    return re.sub(pattern,number,text,flags=re.I)


def disclosure_flags(text,secrets,field_names=()):
    """Conservative bounded detection. Never exempt narrative using an approved action/public value."""
    normalized=expand_number_words(text.translate(str.maketrans({'‑':'-','–':'-','—':'-'}))).lower().replace(',','')
    flags=[]
    for name in field_names:
        if re.search(r'\b'+re.escape(name).replace('_',r'[_ -]')+r'\b',normalized):flags.append('protected_field')
    numbers=[float(m.group()) for m in re.finditer(r'(?<![\w.])-?\d+(?:\.\d+)?(?!\d|\.\d)',normalized)]
    if secrets and numbers and re.search(r'between|range|interval',normalized):flags.append('numeric_range_with_private_context')
    for secret in secrets:
        try:value=float(expand_number_words(str(secret)))
        except (ValueError,TypeError):
            if str(secret).lower() in normalized:flags.append('private_literal')
            continue
        if not math.isfinite(value):continue
        if any(abs(n-value)<1e-8 for n in numbers):flags.append('private_number')
        for match in re.finditer(r'(?:between\s*\$?\s*)?(-?\d+(?:\.\d+)?)\s*(?:and|to|through|[-–—])\s*\$?\s*(-?\d+(?:\.\d+)?)',normalized):
            low,high=map(float,match.groups())
            if min(low,high)<=value<=max(low,high):flags.append('containing_range')
        if re.search(r'floor|minimum|reservation|secret|confidential|private|threshold|budget|cost basis|limit|just under|just below|slightly|round|approximately|percent|%',normalized):
            # Treat boundary/rounding/offset claims about a private quantity as unsafe, even if the
            # particular arithmetic is deliberately obscured. Avoid offering a numerical oracle.
            if numbers:flags.append('private_numeric_inference')
        for scale in (1,5,10,25,50,100):
            rounded=math.floor(value/scale+.5)*scale
            if re.search(r'round|about|approximately|roughly',normalized) and rounded in numbers:flags.append('rounded_private_number')
        if '%' in normalized or 'percent' in normalized:
            for pct in numbers:
                if any(abs(n-value*(1+sign*pct/100))<.011 for n in numbers for sign in (-1,1)):flags.append('percentage_offset')
    return sorted(set(flags))


def redact_model_values(value,secrets,fields=()):
    """Negotiation input boundary: remove protected names/values from all model-visible items."""
    if isinstance(value,dict):return {key:redact_model_values(item,secrets,fields) for key,item in value.items() if key not in fields}
    if isinstance(value,list):return [redact_model_values(item,secrets,fields) for item in value]
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        for secret in secrets:
            try:
                if float(expand_number_words(str(secret)))==value:return None
            except (ValueError,TypeError):pass
        return value
    if isinstance(value,str):
        try:parsed=__import__('json').loads(value)
        except (ValueError,TypeError):parsed=None
        if isinstance(parsed,(dict,list)):return __import__('json').dumps(redact_model_values(parsed,secrets,fields),ensure_ascii=False)
        if disclosure_flags(value,secrets,fields):
            # Whole-field replacement also suppresses spelled, bounded, rounded and offset forms.
            return '[protected study information removed]'
    return value


def safe_reply(secrets,numeric=False):
    if not numeric:return SAFE_REPLY
    values=set()
    for secret in secrets:
        try:values.add(float(expand_number_words(str(secret))))
        except (ValueError,TypeError):pass
    for candidate in range(len(values)+1):
        if candidate not in values:return str(candidate)


def secret_atoms(value):
    atoms=[]
    def collect(item):
        if isinstance(item,dict):
            for child in item.values():collect(child)
        elif isinstance(item,(list,tuple,set)):
            for child in item:collect(child)
        elif isinstance(item,(int,float)) and not isinstance(item,bool):atoms.append(f'{item:g}')
        elif isinstance(item,str):
            if len(item.strip())>=3:atoms.append(item.strip())
            else:
                try:
                    if math.isfinite(float(item)):atoms.append(item)
                except ValueError:pass
    collect(value)
    for atom in list(atoms):
        expanded=expand_number_words(str(atom)).replace(',','')
        atoms.extend(f'{float(m.group()):g}' for m in re.finditer(r'(?<![\w.])-?\d+(?:\.\d+)?(?!\d|\.\d)',expanded))
    return sorted(set(atoms))
