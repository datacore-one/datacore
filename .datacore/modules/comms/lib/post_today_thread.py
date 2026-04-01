#!/usr/bin/env python3
"""Post Today in Privacy thread with statement card image.

Usage:
    python3 post_today_thread.py [--dry-run]
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Load env
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
env_file = DATA_DIR / ".datacore" / "env" / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

sys.path.insert(0, str(DATA_DIR / ".datacore/modules/comms/lib"))
from x_poster import XPoster

DRY_RUN = "--dry-run" in sys.argv

MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
TWEET_URL = "https://api.x.com/2/tweets"

THREAD = [
    {
        "text": 'Russia-backed hackers are breaching Signal and WhatsApp accounts of officials, journalists, and military personnel. Fake "Signal Support" chatbots trick users into surrendering verification codes. The encryption works. The humans don\'t. reuters.com/world/europe/russia-backed-hackers-breach-signal-whatsapp-accounts-officials-journalists-2026-03-09/',
        "image": True,  # statement card on first tweet
    },
    {
        "text": "TriZetto serves 875,000 healthcare providers. Yesterday they confirmed 3.4 million patients' data stolen — names, SSNs, health insurance details. Hackers were inside for a year before anyone noticed. infosecurity-magazine.com/news/trizetto-provider-solutions-breach/",
    },
    {
        "text": "Legal data company LexisNexis confirmed a breach: 3.9 million records including internal Salesforce, AWS, and Oracle credentials, stolen by a group called FulcrumSec. salesforceben.com/lexisnexis-data-breach-salesforce-credentials-exposed-in-3-9m-record-hack/",
    },
    {
        "text": "Fintech lender Figure disclosed a breach after a social engineering attack: 967,000 accounts exposed — names, emails, addresses, dates of birth. dig.watch/updates/figure-fintech-data-breach",
    },
    {
        "text": "Trump's DHS agents have been equipped with Meta AI smart glasses. The glasses record video and audio. The people being recorded have no idea. ICE is already exploring facial recognition integration. aol.com/articles/trump-dhs-agents-wearing-meta-143828856.html",
    },
    {
        "text": 'OpenAI signed the Pentagon contract Anthropic refused — unrestricted AI access for military use. Protesters gathered outside OpenAI\'s HQ, writing in chalk: "Please no legal mass surveillance." theatlantic.com/technology/2026/03/openai-pentagon-contract-spying/686282/',
    },
    {
        "text": 'Anthropic filed two federal lawsuits against the Pentagon yesterday, challenging its designation as a "supply chain risk" — a label historically reserved for Chinese state actors — after refusing to enable mass domestic surveillance. axios.com/2026/03/09/anthropic-sues-pentagon-supply-chain-risk-label',
    },
    {
        "text": 'The UK government is moving to grant ministers the power to rewrite its Online Safety law without a parliamentary vote. "We can act within months, not years," said PM Starmer. politico.eu/article/uk-eyes-sweeping-powers-to-regulate-tech-without-parliamentary-scrutiny/',
    },
    {
        "text": "Congress passed COPPA 2.0 in the Senate and is pushing the KIDS Act in the House — mandatory age verification across social media, games, and adult sites. The result: biometric IDs stored on private servers for millions of adults. biometricupdate.com/202603/age-verification-fight-erupts-as-congress-moves-to-regulate-online-spaces-for-children",
    },
]

STATEMENT_CARD_TEXT = "Signal wasn't hacked.\nYou were."


def generate_statement_card(text: str, output_path: Path) -> Path:
    """Generate statement card using Gemini."""
    import google.generativeai as genai
    from PIL import Image, ImageDraw, ImageFont
    import io

    # Try PIL text rendering first (no API needed, reliable)
    try:
        img = Image.new('RGB', (1200, 675), color=(10, 10, 20))
        draw = ImageDraw.Draw(img)

        # Try to load a bold font, fall back to default
        font_large = None
        font_small = None
        for font_path in [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]:
            if Path(font_path).exists():
                try:
                    font_large = ImageFont.truetype(font_path, 96)
                    font_small = ImageFont.truetype(font_path, 32)
                    break
                except Exception:
                    pass

        if font_large is None:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Draw accent line
        draw.rectangle([80, 80, 120, 595], fill=(0, 180, 255))

        # Draw text
        lines = text.split('\n')
        y = 180
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font_large)
            w = bbox[2] - bbox[0]
            draw.text((160, y), line, font=font_large, fill=(255, 255, 255))
            y += 130

        # FDS branding
        draw.text((160, 580), "@FairDataSociety · Today in Privacy", font=font_small, fill=(100, 140, 180))

        img.save(str(output_path), 'PNG')
        print(f"Statement card generated (PIL): {output_path}")
        return output_path

    except Exception as e:
        print(f"PIL generation failed: {e}")
        raise


def upload_media(image_path: Path, poster: XPoster) -> str:
    """Upload image to X v1.1 media API, return media_id."""
    image_data = image_path.read_bytes()
    b64 = base64.b64encode(image_data).decode()

    # Build OAuth for multipart POST
    nonce = secrets.token_hex(16)
    ts = str(int(time.time()))

    api_key = os.environ.get("FDS_X_API_KEY", "")
    api_secret = os.environ.get("FDS_X_API_SECRET", "")
    access_token = os.environ.get("FDS_X_ACCESS_TOKEN", "")
    access_secret = os.environ.get("FDS_X_ACCESS_TOKEN_SECRET", "")

    def percent_encode(s):
        return urllib.parse.quote(str(s), safe='')

    oauth_params = {
        'oauth_consumer_key': api_key,
        'oauth_nonce': nonce,
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': ts,
        'oauth_token': access_token,
        'oauth_version': '1.0',
    }

    # Signature for multipart (no body params in base string)
    base_params = '&'.join(
        f"{percent_encode(k)}={percent_encode(v)}"
        for k, v in sorted(oauth_params.items())
    )
    base_string = f"POST&{percent_encode(MEDIA_UPLOAD_URL)}&{percent_encode(base_params)}"
    signing_key = f"{percent_encode(api_secret)}&{percent_encode(access_secret)}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()

    oauth_params['oauth_signature'] = sig
    auth_header = 'OAuth ' + ', '.join(
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )

    # Multipart form data
    boundary = '----FormBoundary' + secrets.token_hex(8)
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="media_data"\r\n\r\n'
        f'{b64}\r\n'
        f'--{boundary}--\r\n'
    ).encode()

    req = Request(MEDIA_UPLOAD_URL, data=body, method='POST')
    req.add_header('Authorization', auth_header)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

    with urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())

    media_id = result['media_id_string']
    print(f"Media uploaded: {media_id}")
    return media_id


def post_with_media(text: str, media_id: str, poster: XPoster) -> str:
    """Post tweet with media attachment, return tweet ID."""
    payload = {'text': text, 'media': {'media_ids': [media_id]}}
    result = poster._oauth_post(TWEET_URL, payload)
    tweet_id = result['data']['id']
    print(f"Posted with image: {tweet_id}")
    return tweet_id


def main():
    poster = XPoster(account='fds')

    # Generate statement card
    card_path = DATA_DIR / "2-projectspace/2-projects/today-in-privacy-card.png"
    card_path.parent.mkdir(parents=True, exist_ok=True)

    print("Generating statement card...")
    generate_statement_card(STATEMENT_CARD_TEXT, card_path)

    if DRY_RUN:
        print("\n--- DRY RUN ---")
        for i, tweet in enumerate(THREAD):
            prefix = f"[T{i+1}{'🖼' if tweet.get('image') else ''}] "
            print(f"{prefix}{tweet['text'][:120]}...")
            print(f"  ({len(tweet['text'])} chars)")
        return

    # Upload statement card
    print("Uploading statement card...")
    media_id = upload_media(card_path, poster)

    # Post thread
    thread_id = None
    for i, tweet in enumerate(THREAD):
        text = tweet['text']
        print(f"\nPosting tweet {i+1}/{len(THREAD)}...")

        if i == 0:
            # First tweet with image
            tweet_id = post_with_media(text, media_id, poster)
            thread_id = tweet_id
        else:
            result = poster.reply(text, thread_id)
            tweet_id = result['data']['id']
            thread_id = tweet_id
            print(f"Reply posted: {tweet_id}")

        time.sleep(3)

    print(f"\nThread posted. First tweet ID: {thread_id}")
    print(f"https://x.com/FairDataSociety/status/{thread_id}")


if __name__ == "__main__":
    main()
