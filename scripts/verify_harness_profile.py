"""向本机模拟 Messages 端点验证真实 DSH 二进制的有效配置，不接触 .env、不调用真实模型。"""
import json
from pathlib import Path
import tempfile
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deepseek_harness import DeepSeekHarness
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.harness import HarnessRunner


def verify():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            frames = [
                ('message_start', {'type':'message_start','message':{'id':'mock-only','type':'message','role':'assistant',
                    'content':[],'model':'deepseek-flash','stop_reason':None,'usage':{'input_tokens':10,'output_tokens':0}}}),
                ('content_block_start', {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}),
                ('content_block_delta', {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'{"ok":true}'}}),
                ('content_block_stop', {'type':'content_block_stop','index':0}),
                ('message_delta', {'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':4}}),
                ('message_stop', {'type':'message_stop'}),
            ]
            data = ''.join('event: '+kind+'\ndata: '+json.dumps(value)+'\n\n' for kind,value in frames).encode()
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='dsh-profile-proof-') as temporary:
            class RecordedHarness:
                def __init__(self, **options):
                    options['base_url']=f'http://127.0.0.1:{server.server_address[1]}'
                    self.inner=DeepSeekHarness(**options)
                    self.results=[]
                def start_session(self, session_id):
                    session=self.inner.start_session(session_id)
                    def run(prompt):
                        result=session.run(prompt)
                        self.results.append(result)
                        return result
                    return SimpleNamespace(run=run)
                def close(self):
                    self.inner.close()
            runner=HarnessRunner(api_key='sk-'+'0'*32,model='deepseek-flash',root=Path(temporary),
                                 request_timeout=20,harness_factory=RecordedHarness)
            try:
                assert runner.complete('preflight','Return JSON only.','Return {"ok":true}',{
                    'type':'object','required':['ok'],'properties':{'ok':{'type':'boolean','enum':[True]}}
                })=={'ok':True}
                result=runner.client.results[0]
                assert json.loads(result.final_response)=={'ok':True}
                assert result.finish_reason=='completed'
                assert len(requests)==1
                body=requests[0]
                assert body.get('tools',[])==[], '仍然向模型开放了工具'
                assert 'dsh_session_log' not in body, '仍然上报额外会话日志'
                assert not any('inventory' in name for name in body), '仍然上报插件清单'
                types=[event.get('type') for event in result.events]
                usage_events=[event for event in result.events if 'usage' in str(event.get('type'))]
                print(json.dumps({'status':'passed','runtime':'deepseek-harness-sdk','profile':'sdk-minimal',
                    'tool_count':len(body.get('tools',[])),'session_log_upload':False,'plugin_inventory_upload':False,
                    'real_model_calls':0,'event_types':types,'usage_events':usage_events,
                    'adapter_usage':runner.budget.summary(),'usage_complete':runner.token_usage_complete},ensure_ascii=False))
            finally:
                runner.client.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__=='__main__':
    verify()
