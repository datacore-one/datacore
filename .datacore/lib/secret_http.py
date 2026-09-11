"""Authenticated API requests stay on their configured HTTPS origin."""
import io
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from relay_client import NoRedirect


class BoundedResponse:
    def __init__(self, response):
        self.response = response
        self.remaining = 16 * 1024 * 1024

    def read(self, size=-1):
        data = self.response.read(min(size, self.remaining + 1) if size >= 0 else self.remaining + 1)
        self.remaining -= len(data)
        if self.remaining < 0:
            self.response.close()
            raise ValueError('API response exceeds size limit')
        return data

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()


def urlopen(request, *, timeout=30, allow_loopback=False):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    parsed = urlsplit(url)
    local = allow_loopback and parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1'}
    if (parsed.scheme != 'https' and not local) or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('authenticated APIs require an HTTPS endpoint without userinfo')
    # Redirects and ambient proxies must not receive bearer/API-key headers.
    try:
        response = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({})).open(request, timeout=timeout)
        return BoundedResponse(response)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        # URLs can contain API keys and providers can reflect credentials in
        # bodies/headers. Keep the status contract without exporting either.
        raise urllib.error.HTTPError('https://redacted.invalid/', code, 'API request failed', {}, io.BytesIO(b'{}')) from None
    except (urllib.error.URLError, OSError):
        raise urllib.error.URLError('API connection failed') from None
    except (ValueError, UnicodeError):
        # http.client includes the complete header value in validation errors.
        raise ValueError('invalid authenticated API request') from None
