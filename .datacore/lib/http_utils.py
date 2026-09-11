"""Shared limits for the small, authenticated HTTP receivers."""
import hmac
import socket
import sys
import threading
from http.server import ThreadingHTTPServer


class RequestError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def bearer_matches(headers, secret):
    """Empty configuration always denies access; compare secret bytes safely."""
    values = headers.get_all("Authorization", [])
    if not secret or len(values) != 1 or not values[0].startswith("Bearer "):
        return False
    return hmac.compare_digest(values[0][7:].encode("utf-8"), secret.encode("utf-8"))


def read_body(handler, limit=1_000_000):
    """Accept one explicit decimal length, bounded and read completely."""
    if handler.headers.get_all("Transfer-Encoding"):
        raise RequestError(400, "transfer encoding is unsupported")
    values = handler.headers.get_all("Content-Length", [])
    if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal():
        raise RequestError(400, "one Content-Length is required")
    if len(values[0]) > 10:
        raise RequestError(413, "body too large")
    length = int(values[0])
    if length > limit:
        raise RequestError(413, "body too large")
    try:
        raw = handler.rfile.read(length)
    except OSError as exc:
        raise RequestError(408, "request body timed out") from exc
    if len(raw) != length:
        raise RequestError(400, "incomplete body")
    return raw


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound simultaneous handlers and time spent waiting for request bytes."""
    daemon_threads = True
    request_timeout = 10

    def __init__(self, *args, max_workers=16, **kwargs):
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout)
        return connection, address

    def handle_error(self, request, client_address):
        # A client disconnect (including our deadline) is normal transport
        # failure; other handler errors still receive the standard traceback.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        # Socket timeouts alone reset for each recv; a client can otherwise
        # keep a worker forever by trickling bytes just before every timeout.
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # already closed by the handler
        deadline = threading.Timer(self.request_timeout, expire)
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self._slots.release()
