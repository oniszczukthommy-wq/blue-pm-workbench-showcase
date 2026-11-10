"""Run the original Agent Workshop in its own process and import environment."""
import os
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1]
upstream = root / 'integrations' / 'agent-workshop'
sys.path.insert(0, str(upstream))
os.chdir(upstream)
from blueprint.web.server import App, make_handler  # noqa: E402

output = Path(os.environ.get('BLUEPRINT_OUT', root / 'data' / 'agent-workshop'))
app = App(output)
OriginalHandler = make_handler(app)

class Handler(OriginalHandler):
    def do_GET(self):
        if self.path == '/__blue_workshop_identity__':
            if not self._host_ok() or not self._origin_ok():
                return self._error(403, '只允许从本机访问')
            return self._json({'service': 'blue-original-agent-workshop-v1'})
        return super().do_GET()

ThreadingHTTPServer(('127.0.0.1', 18765), Handler).serve_forever()
