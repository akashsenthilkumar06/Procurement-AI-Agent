"""Live-only recorder using the existing HTTP adapter, run inside a killable subprocess."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research_agent.providers import _agent_step_http,ProviderError
import urllib.request

if __name__=='__main__':
    if os.environ.get('RESEARCH_AGENT_LIVE_TESTS')!='1' or not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('Live provider worker is disabled')
    args=json.load(sys.stdin);capture=Path(args.pop('capture_path'))
    original=urllib.request.urlopen
    def record(request,**kwargs):
        # Save exactly what the adapter sends; never save authorization headers/environment.
        capture.write_text(json.dumps({'request':json.loads(request.data)},indent=2))
        capture.chmod(0o600)
        return original(request,**kwargs)
    try:
        with patch('research_agent.providers.urllib.request.urlopen',side_effect=record):response,metadata=_agent_step_http(**args)
        print(json.dumps({'response':response,'metadata':metadata},allow_nan=False))
    except ProviderError as exc:print(json.dumps({'error':str(exc)}))
