"""Server-only provider adapters for legacy responses and bounded tool-loop steps."""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


class ProviderError(Exception):
    pass


class ProviderTimeout(ProviderError):
    pass


class OpenAIProvider:
    def generate(self, config, condition, items):
        architecture = config["architecture"]
        negotiation = config.get("task") == "negotiation"
        props = {"answer": {"type": "string"}, "option_id": {
            "type": ["string", "null"], "enum": [None] if negotiation else [None]+[o["id"] for o in config["scenario"]["options"]]}}
        if negotiation:
            props["offer_price"] = {"type": ["number", "null"]}
        schema = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
        body = {"model": architecture["model"], "input": items, "store": False,
                "service_tier": "default", "truncation": "disabled",
                "max_output_tokens": architecture.get("max_output_tokens", 1200),
                "text": {"format": {"type": "json_schema", "name": "study_response", "strict": True, "schema": schema}}}
        if architecture["temperature"] is not None:
            body["temperature"] = architecture["temperature"]
        if architecture["reasoning_effort"] is not None:
            body["reasoning"] = {"effort": architecture["reasoning_effort"]}
        result = request_openai(body, timeout=60)
        if result.get("status") != "completed":
            raise ProviderError("Provider did not complete the response")
        if result.get("model") != architecture["model"]:
            raise ProviderError("Provider returned a different model snapshot; response blocked")
        text = "".join(part.get("text", "") for item in result.get("output", []) if item.get("type") == "message"
                       for part in item.get("content", []) if part.get("type") == "output_text")
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict) or set(parsed) != set(props) or not isinstance(parsed["answer"], str) or not parsed["answer"].strip() or len(parsed["answer"]) > 16000:
                raise ValueError()
            if parsed["option_id"] not in props["option_id"]["enum"]:
                raise ValueError()
        except (ValueError, TypeError):
            raise ProviderError("Provider returned invalid structured output; response blocked") from None
        return parsed, {"provider_response_id": result.get("id"), "returned_model": result.get("model"), "usage": result.get("usage")}


PROVIDERS = {"openai": OpenAIProvider()}


def openai_ssl_context():
    # The macOS Python.org installer may leave OpenSSL without a CA bundle.
    if sys.platform == "darwin":
        paths = ssl.get_default_verify_paths()
        system_bundle = "/etc/ssl/cert.pem"
        if (not paths.cafile and not paths.capath
                and not os.environ.get(paths.openssl_cafile_env) and not os.environ.get(paths.openssl_capath_env)
                and os.path.isfile(system_bundle)):
            return ssl.create_default_context(cafile=system_bundle)
    return None


def request_openai(body, timeout):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ProviderError("OPENAI_API_KEY is not configured on the server")
    request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=max(.01, timeout), context=openai_ssl_context()) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        guidance = {
            401: "Check the server’s OpenAI API key.",
            403: "Check the OpenAI project’s model and Responses API permissions.",
            429: "Check the OpenAI project’s usage limits and billing.",
        }.get(exc.code)
        message = f"OpenAI request failed (HTTP {exc.code})."
        raise ProviderError(f"{message} {guidance}" if guidance else message) from None
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise ProviderTimeout("Model step exceeded the turn timeout") from None
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise ProviderError("OpenAI certificate verification failed. Check the server’s Python CA bundle.") from None
        raise ProviderError("Provider response unavailable") from None


def _agent_step_http(config, condition, items, tool_definitions, decision_schema, timeout):
    """One Responses step; orchestration and execution remain in our server."""
    architecture=config['architecture']
    from .agent import authority_contract
    from .packs import resolve
    contract=authority_contract(condition);pack,_=resolve(config['case_study'])
    can_propose=contract['propose'] and not pack.get('offer_policy')
    action_schema=decision_schema if can_propose else {'type':'null'}
    body={'model':architecture['model'],'input':items,'store':False,'parallel_tool_calls':False,
        'service_tier':'default','truncation':'disabled','temperature':architecture['temperature'],
        'max_output_tokens':architecture['max_output_tokens'],'tools':tool_definitions,
        'text':{'format':{'type':'json_schema','name':'agent_answer','strict':True,'schema':{
            'type':'object','properties':{'answer':({'type':'string','pattern':r'^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$'} if contract['output_type']=='number' else {'type':'string'}),'action':{'anyOf':[action_schema,{'type':'null'}]} if can_propose else {'type':'null'}},
            'required':['answer','action'],'additionalProperties':False}}}}
    if architecture['reasoning_effort'] is not None:body['reasoning']={'effort':architecture['reasoning_effort']}
    result = request_openai(body, timeout)
    if result.get('status')!='completed' or result.get('model')!=architecture['model']:raise ProviderError('Provider did not complete using the pinned snapshot')
    output=result.get('output',[]);calls=[];texts=[]
    for item in output:
        if item.get('type')=='function_call':
            try:arguments=json.loads(item['arguments'])
            except (ValueError,TypeError):raise ProviderError('Invalid tool arguments') from None
            calls.append({'name':item['name'],'inputs':arguments,'call_id':item['call_id']})
        elif item.get('type')=='message':texts.extend(part['text'] for part in item.get('content',[]) if part.get('type')=='output_text')
        else:raise ProviderError('Unexpected provider output type')
    final=None
    if not calls:
        try:final=json.loads(''.join(texts))
        except (ValueError,TypeError):raise ProviderError('Invalid agent answer') from None
    return {'calls':calls,'items':output,'final':final},{'returned_model':result['model'],'provider_response_id':result.get('id'),'usage':result.get('usage')}


def agent_step(config,condition,items,tool_definitions,decision_schema,timeout):
    """Killable wall-clock boundary; uncertain API costs retain their reserved allowance."""
    import subprocess
    import sys
    from pathlib import Path
    payload={'config':config,'condition':condition,'items':items,'tool_definitions':tool_definitions,'decision_schema':decision_schema,'timeout':timeout}
    try:
        child=subprocess.run([sys.executable,'-m','research_agent.providers'],input=json.dumps(payload),text=True,capture_output=True,timeout=max(.01,timeout),cwd=str(Path(__file__).resolve().parent.parent))
    except subprocess.TimeoutExpired:raise ProviderTimeout('Model step exceeded the turn timeout') from None
    if child.returncode:raise ProviderError('Provider worker failed')
    try:result=json.loads(child.stdout)
    except ValueError:raise ProviderError('Invalid provider worker response') from None
    if result.get('timeout'):raise ProviderTimeout(result['error'])
    if 'error' in result:raise ProviderError(result['error'])
    return result['response'],result['metadata']


if __name__=='__main__':
    import sys
    try:
        args=json.load(sys.stdin);response,metadata=_agent_step_http(**args)
        print(json.dumps({'response':response,'metadata':metadata},allow_nan=False))
    except ProviderError as exc:print(json.dumps({'error':str(exc), 'timeout':isinstance(exc, ProviderTimeout)}))
