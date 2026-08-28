#!/usr/bin/env python3
"""Post to X (api.x.com v2) with OAuth 1.0a.

Extracted from `scripts/release.sh` in the PLUR monorepo, which could only tweet
as part of a version release. Anything else worth announcing — a new plugin, a
blog post, a reply in someone else's thread — had no path, so this is the same
credentials and the same endpoint with the release coupling removed.

Credentials come from a space env file (default `5-plur`), never from argv, so a
token cannot end up in shell history or a process listing.

Usage:
    x_post.py --text "hello"                        # post
    x_post.py --text "…" --reply-to 1234567890      # reply to a tweet
    x_post.py --text "…" --quote 1234567890         # quote-tweet
    x_post.py --text "…" --dry-run                  # print, post nothing
    x_post.py --file draft.txt                      # read body from a file

Exit codes: 0 posted (or dry run), 1 refused/failed.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import re
import string
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SECRETS = Path.home() / 'Data/.datacore/secrets/spaces'
ENDPOINT = 'https://api.x.com/2/tweets'
#: X's own limit.
MAX_CHARS = 280
#: Every link counts as this many characters however long it is — X rewrites it
#: through t.co. Counting raw length rejects tweets that would actually fit.
TCO_LENGTH = 23
URL_PATTERN = re.compile(r'https?://\S+|(?<![@\w.])(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?', re.I)


def weighted_length(text: str) -> int:
    """Length as X counts it, with every URL charged at t.co length.

    :param text: the tweet body.
    :returns: the counted length.
    """
    return len(URL_PATTERN.sub('#' * TCO_LENGTH, text))

REQUIRED = (
    'PLUR_X_API_KEY',
    'PLUR_X_API_SECRET',
    'PLUR_X_ACCESS_TOKEN',
    'PLUR_X_ACCESS_TOKEN_SECRET',
)


def load_credentials(space: str) -> dict[str, str]:
    """Read the four OAuth values from a space's env file.

    :param space: space name, e.g. ``5-plur``.
    :returns: the credentials.
    :raises SystemExit: when the file or any value is missing.
    """
    path = SECRETS / f'{space}.env'
    if not path.exists():
        sys.exit(f'No credential file at {path}')
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in REQUIRED if not values.get(k)]
    if missing:
        sys.exit(f'Missing in {path}: {", ".join(missing)}')
    return {k: values[k] for k in REQUIRED}


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe='')


def sign(creds: dict[str, str]) -> str:
    """Build the OAuth 1.0a Authorization header for a POST to ENDPOINT.

    The JSON body is NOT part of the signature base string — OAuth 1.0a signs
    only oauth_* parameters for a non-form-encoded body, which is why the
    payload can carry arbitrary text.

    :param creds: the four OAuth values.
    :returns: the header value.
    """
    params = {
        'oauth_consumer_key': creds['PLUR_X_API_KEY'],
        'oauth_nonce': ''.join(random.choices(string.ascii_letters + string.digits, k=32)),
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': str(int(time.time())),
        'oauth_token': creds['PLUR_X_ACCESS_TOKEN'],
        'oauth_version': '1.0',
    }
    joined = '&'.join(f'{_quote(k)}={_quote(params[k])}' for k in sorted(params))
    base = '&'.join(['POST', _quote(ENDPOINT), _quote(joined)])
    key = f"{_quote(creds['PLUR_X_API_SECRET'])}&{_quote(creds['PLUR_X_ACCESS_TOKEN_SECRET'])}"
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    params['oauth_signature'] = __import__('base64').b64encode(digest).decode()
    return 'OAuth ' + ', '.join(f'{_quote(k)}="{_quote(params[k])}"' for k in sorted(params))


def post(text: str, creds: dict[str, str], reply_to: str | None, quote: str | None) -> dict:
    """Post one tweet.

    :param text: the body.
    :param creds: OAuth values.
    :param reply_to: tweet id to reply to, or None.
    :param quote: tweet id to quote, or None.
    :returns: the parsed API response.
    """
    payload: dict[str, object] = {'text': text}
    if reply_to:
        payload['reply'] = {'in_reply_to_tweet_id': str(reply_to)}
    if quote:
        payload['quote_tweet_id'] = str(quote)
    body = json.dumps(payload).encode()
    request = urllib.request.Request(ENDPOINT, data=body, method='POST', headers={
        'Authorization': sign(creds),
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        sys.exit(f'X refused ({error.code}): {error.read().decode()[:400]}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--file', type=Path, help='read the body from this file')
    parser.add_argument('--reply-to', help='tweet id this replies to')
    parser.add_argument('--quote', help='tweet id to quote')
    parser.add_argument('--space', default='5-plur')
    parser.add_argument('--dry-run', action='store_true', help='print and exit')
    args = parser.parse_args()

    text = args.file.read_text().rstrip('\n') if args.file else args.text
    length = weighted_length(text)

    print('─' * 60)
    print(text)
    print('─' * 60)
    raw = len(text)
    print(f'{length} characters as X counts them' + (f' ({raw} raw)' if raw != length else ''), end='')
    if args.reply_to:
        print(f' · reply to {args.reply_to}', end='')
    if args.quote:
        print(f' · quoting {args.quote}', end='')
    print()

    if length > MAX_CHARS:
        sys.exit(f'Refusing: {length} characters, {length - MAX_CHARS} over the {MAX_CHARS} limit.')
    if args.dry_run:
        print('DRY RUN — nothing posted.')
        return

    result = post(text, load_credentials(args.space), args.reply_to, args.quote)
    tweet_id = result.get('data', {}).get('id')
    print(f'POSTED: https://x.com/plur_ai/status/{tweet_id}')


if __name__ == '__main__':
    main()
