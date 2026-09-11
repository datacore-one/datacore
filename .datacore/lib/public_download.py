"""Bounded HTTP downloads that cannot reach local or private addresses."""
import http.client
import ipaddress
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit
from bounded_dns import resolve

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 30


def public_addresses(host: str, port: int, *, timeout=MAX_SECONDS):
    addresses = resolve(host, port, timeout=timeout)
    if not addresses:
        raise ValueError("URL host has no address")
    for family, kind, proto, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or getattr(ip, "ipv4_mapped", None)
                or getattr(ip, "sixtofour", None) or getattr(ip, "teredo", None)):
            raise ValueError("URL must resolve only to public addresses")
    return addresses


def parse_public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("expected a public HTTP(S) URL without credentials")
    if any(ord(c) < 33 for c in url):
        raise ValueError("invalid URL characters")
    return parsed


def _fetch(url: str, *, max_bytes=MAX_BYTES, headers=None, metadata_only=False):
    """Pin the validated DNS result for each redirect and verify HTTPS normally.

    No ambient proxy or credentials are used. Explicit headers are sent only
    to the initial HTTPS origin; redirects cannot forward them elsewhere.
    The original hostname remains the TLS SNI/certificate and Host identity.
    """
    deadline = time.monotonic() + MAX_SECONDS
    active_headers = dict(headers or {})
    origin = None
    for _ in range(4):
        parsed = parse_public_url(url)
        host = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        current_origin = (parsed.scheme, host, port)
        if origin is None:
            origin = current_origin
            if active_headers and parsed.scheme != "https":
                raise ValueError("explicit download headers require HTTPS")
        elif current_origin != origin:
            active_headers.clear()
        addresses = public_addresses(host, port, timeout=deadline - time.monotonic())
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("download deadline exceeded")
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(host, port, timeout=min(remaining, 5))
        expired = threading.Event()

        def expire():
            expired.set()
            # HTTPResponse may own the socket after HTTPConnection clears
            # its reference (Connection: close). Retain a duplicate solely
            # for shutdown so slow headers/bodies cannot reset the deadline.
            with socket_lock:
                for control in controls:
                    try:
                        control.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        controls = []
        socket_lock = threading.Lock()

        def connect(_address, timeout, source_address=None):
            # Connect to already checked numeric addresses; no second DNS
            # lookup can redirect a validated hostname into the local network.
            last_error = None
            for family, kind, proto, _, address in addresses:
                sock = socket.socket(family, kind, proto)
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("download deadline exceeded")
                    sock.settimeout(min(remaining, timeout))
                    sock.connect(address)
                    with socket_lock:
                        if expired.is_set():
                            raise TimeoutError('download deadline exceeded')
                        controls.append(sock.dup())
                    return sock
                except OSError as error:
                    last_error = error
                    sock.close()
            raise last_error or OSError("cannot connect")

        connection._create_connection = connect
        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection.request("HEAD" if metadata_only else "GET", path, headers={"User-Agent": "Datacore/1.0", **active_headers, "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if metadata_only and response.status == 405:
                connection.close()
                connection.request("GET", path, headers={"User-Agent": "Datacore/1.0", "Accept-Encoding": "identity"})
                response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("redirect has no destination")
                url = urljoin(url, location)
                continue
            if metadata_only:
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError('download deadline exceeded')
                return response.status, response.getheader("Content-Type", ""), url
            if response.status != 200:
                raise ValueError("server returned an unsuccessful status")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdecimal() or len(length) > 10 or int(length) > max_bytes):
                raise ValueError("download exceeds size limit")
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("download deadline exceeded")
                if connection.sock is not None:
                    connection.sock.settimeout(min(remaining, 5))
                chunk = response.read1(min(65536, max_bytes + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("download exceeds size limit")
            if length is not None and len(data) != int(length):
                raise ValueError("incomplete download")
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError('download deadline exceeded')
            return bytes(data)
        except (OSError, ValueError, http.client.HTTPException):
            if expired.is_set():
                raise TimeoutError('download deadline exceeded') from None
            raise
        finally:
            timer.cancel()
            timer.join()
            connection.close()
            for control in controls:
                control.close()
    raise ValueError("too many redirects")


def download(url: str, *, max_bytes=MAX_BYTES, headers=None) -> bytes:
    return _fetch(url, max_bytes=max_bytes, headers=headers)


def probe(url: str):
    """Fetch public response metadata without consuming a potentially large body."""
    return _fetch(url, metadata_only=True)
