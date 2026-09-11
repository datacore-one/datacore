"""One authenticated relay request path, with explicit acknowledgement."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post_event(row, url, token, *, timeout=4):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        request = urllib.request.Request(url.rstrip('/') + '/events', method='POST',
            data=json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
        with urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
            result = json.loads(response.read(65537))
            return 200 <= response.status < 300 and isinstance(result, dict) and result.get('ok') is True
    except (urllib.error.URLError, OSError, ValueError):
        return False
