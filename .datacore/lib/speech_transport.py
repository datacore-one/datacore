"""Opt-in cloud speech with verified TLS and preservation on partial failure.

gTTS supplies its public request-body formatter, not its transport: 2.5.4's
stream() disables certificate verification and save() truncates the target
before a request succeeds. Keep those invariants in this boundary instead.
"""
from __future__ import annotations

import base64
import binascii
import json
import http.client
import os
import tempfile
import time
from pathlib import Path

from public_download import post as public_post

ENDPOINT = 'https://translate.google.com/_/TranslateWebserverUi/data/batchexecute'
MAX_TEXT = 20000
MAX_RESPONSE = 1024 * 1024
MAX_AUDIO = 20 * 1024 * 1024
TOTAL_TIMEOUT = 120


def _audio_from_response(body: bytes) -> bytes:
    """Decode only the expected RPC response; framing lines carry no audio."""
    parts = []
    try:
        for line in body.decode('utf-8').splitlines():
            if not line.startswith('['):
                continue
            records = json.loads(line)
            if not isinstance(records, list):
                raise ValueError('invalid RPC response')
            for record in records:
                if not isinstance(record, list) or len(record) < 3 or record[:2] != ['wrb.fr', 'jQ1olc']:
                    continue
                result = json.loads(record[2])
                if not isinstance(result, list) or not result or not isinstance(result[0], str):
                    raise ValueError('invalid audio response')
                parts.append(base64.b64decode(result[0], validate=True))
    except (UnicodeError, ValueError, TypeError, binascii.Error):
        raise RuntimeError('cloud speech returned invalid audio') from None
    audio = b''.join(parts)
    if not audio:
        raise RuntimeError('cloud speech returned no audio')
    return audio


def synthesize_google(text: str, output_path=None, *, allow_cloud: bool = False) -> Path:
    if allow_cloud is not True:
        raise RuntimeError('Cloud speech is disabled')
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f'cloud speech requires 1–{MAX_TEXT} characters')
    from gtts import gTTS

    # No language-discovery request may precede the verified transport.
    bodies = gTTS(text=text, lang='en', slow=False, lang_check=False).get_bodies()
    if not bodies or len(bodies) > MAX_TEXT:
        raise RuntimeError('cloud speech produced an invalid request batch')
    target = Path(output_path) if output_path is not None else None
    directory = target.parent if target is not None else None
    fd, temporary = tempfile.mkstemp(prefix='.datacore-speech-', suffix='.mp3', dir=directory)
    staged = Path(temporary)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    published = False
    try:
        with os.fdopen(fd, 'wb') as out:
            size = 0
            for body in bodies:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('cloud speech exceeded its time limit')
                try:
                    content = public_post(ENDPOINT, body.encode('ascii'), headers={
                        'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
                        'Referer': 'https://translate.google.com/',
                    }, max_bytes=MAX_RESPONSE, timeout=min(30, remaining))
                except (OSError, ValueError, http.client.HTTPException):
                    # Provider response/request objects may include briefing
                    # text. Report failure without copying them into logs.
                    raise RuntimeError('cloud speech HTTPS request failed') from None
                audio = _audio_from_response(content)
                size += len(audio)
                if size > MAX_AUDIO:
                    raise RuntimeError('cloud speech audio exceeds its size limit')
                out.write(audio)
            out.flush()
            os.fsync(out.fileno())
        if target is not None:
            os.replace(staged, target)
            dirfd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        else:
            target = staged
        published = True
        return target
    finally:
        if not published:
            staged.unlink(missing_ok=True)
