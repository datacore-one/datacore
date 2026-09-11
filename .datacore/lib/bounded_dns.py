"""Run the platform resolver in a disposable process with a hard deadline.

getaddrinfo has no per-call timeout. A thread timeout leaves the blocked resolver
alive; the separate process lets us terminate and reap it instead.
"""
import ipaddress
import json
import socket
import subprocess
import sys

from process_run import run

_RESOLVE = """
import json, socket, sys
rows = socket.getaddrinfo(sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM)
if len(rows) > 256:
    raise ValueError('too many DNS answers')
print(json.dumps(rows))
"""


def resolve(host, port, *, timeout):
    if timeout <= 0:
        raise TimeoutError('DNS deadline exceeded')
    # Numeric addresses need no resolver, including denied loopback/private
    # literals. The caller still validates their public-address policy.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        target = (str(address), port, 0, 0) if address.version == 6 else (str(address), port)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', target)]
    try:
        result = run([sys.executable, '-I', '-c', _RESOLVE, host, str(port)],
                     capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        raise TimeoutError('DNS deadline exceeded') from None
    except subprocess.CalledProcessError:
        raise OSError('DNS resolution failed') from None
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) > 256:
        raise ValueError('invalid DNS response')
    return [(family, kind, protocol, name, tuple(address))
            for family, kind, protocol, name, address in rows]
