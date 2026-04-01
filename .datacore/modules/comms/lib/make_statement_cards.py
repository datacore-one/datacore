#!/usr/bin/env python3
"""Generate FDS statement cards for 7 scheduled tweets."""
from PIL import Image, ImageDraw, ImageFont
import textwrap
import os

OUTPUT_DIR = os.path.expanduser("~/Data/1-datafund/1-tracks/comms/statement-cards")
os.makedirs(OUTPUT_DIR, exist_ok=True)

WIDTH, HEIGHT = 1200, 675
BG_COLOR = (13, 17, 23)          # #0d1117 — deep charcoal
TEXT_COLOR = (255, 255, 255)      # white
ACCENT_COLOR = (158, 255, 192)    # #9effc0 — FDS green
DIM_COLOR = (120, 130, 145)       # dim grey for handle

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

TWEETS = [
    {
        "filename": "2026-03-09-dropbox-readable.png",
        "date": "Mon Mar 9",
        "text": "Every file in Dropbox, Google Drive, or iCloud is readable by the company, their engineers, and anyone with a subpoena.\n\nNot a conspiracy. Not a bug. The architecture.",
    },
    {
        "filename": "2026-03-10-rights-paperwork.png",
        "date": "Tue Mar 10",
        "text": "Rights without architecture are just paperwork.\n\nGDPR gave you the right to your data. It did not give you the infrastructure to act on it.",
    },
    {
        "filename": "2026-03-11-ai-assistant.png",
        "date": "Wed Mar 11",
        "text": "Your AI assistant: the question isn't can it help, but who else reads what you show it.",
    },
    {
        "filename": "2026-03-12-age-verification.png",
        "date": "Thu Mar 12",
        "text": "You can prove you're over 18 without revealing your name or identity.\n\nThat's not science fiction. The KIDS Act doesn't require it. It should.",
    },
    {
        "filename": "2026-03-13-policy-acquisition.png",
        "date": "Fri Mar 13",
        "text": "Privacy by policy means privacy until someone buys the company.",
    },
    {
        "filename": "2026-03-14-same-expansion.png",
        "date": "Sat Mar 14",
        "text": "Your email was read for ads. Your metadata was sold to data brokers. Now age verification laws want your ID before you can post.\n\nSame expansion, new layer.",
    },
    {
        "filename": "2026-03-15-fairdrop-architecture.png",
        "date": "Sun Mar 15",
        "text": "Dropbox can read every file you store there. Fairdrop can't — the files are encrypted before they leave your device.\n\nSame convenience, different architecture.",
    },
]


def make_card(tweet: dict):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    PAD = 80
    ACCENT_H = 4
    BOTTOM_RESERVE = 70

    # Accent bar at top
    draw.rectangle([PAD, 48, PAD + 60, 48 + ACCENT_H], fill=ACCENT_COLOR)

    # Text area bounds
    text_x = PAD
    text_y_start = 80
    text_max_w = WIDTH - 2 * PAD
    text_max_h = HEIGHT - text_y_start - BOTTOM_RESERVE - 40

    # Try font sizes from large down until text fits
    text = tweet["text"]
    font_size = 52
    font = ImageFont.truetype(FONT_BOLD, font_size)

    while font_size > 24:
        font = ImageFont.truetype(FONT_BOLD, font_size)
        # Wrap at ~char width that fits the box
        avg_char_w = font_size * 0.55
        wrap_width = max(20, int(text_max_w / avg_char_w))

        # Handle pre-existing line breaks
        paragraphs = text.split("\n\n")
        wrapped_lines = []
        for para in paragraphs:
            if not para.strip():
                wrapped_lines.append("")
                continue
            lines = para.strip().split("\n")
            for line in lines:
                wrapped = textwrap.wrap(line, width=wrap_width)
                wrapped_lines.extend(wrapped if wrapped else [""])
            wrapped_lines.append("")  # blank line between paragraphs

        # Remove trailing blank
        while wrapped_lines and wrapped_lines[-1] == "":
            wrapped_lines.pop()

        line_h = font_size * 1.45
        total_h = len(wrapped_lines) * line_h

        if total_h <= text_max_h:
            break
        font_size -= 2

    # Vertical center in available space
    available_h = HEIGHT - text_y_start - BOTTOM_RESERVE
    start_y = text_y_start + (available_h - total_h) / 2
    start_y = max(text_y_start, start_y)

    # Draw text
    y = start_y
    for line in wrapped_lines:
        if line == "":
            y += line_h * 0.4
            continue
        draw.text((text_x, y), line, font=font, fill=TEXT_COLOR)
        y += line_h

    # Bottom: @FairDataSociety handle
    small_font = ImageFont.truetype(FONT_REG, 22)
    handle = "@FairDataSociety"
    draw.text((PAD, HEIGHT - 45), handle, font=small_font, fill=ACCENT_COLOR)

    # Bottom right: fairdatasociety.org
    url = "fairdatasociety.org"
    bbox = draw.textbbox((0, 0), url, font=small_font)
    url_w = bbox[2] - bbox[0]
    draw.text((WIDTH - PAD - url_w, HEIGHT - 45), url, font=small_font, fill=DIM_COLOR)

    out = os.path.join(OUTPUT_DIR, tweet["filename"])
    img.save(out, "PNG", optimize=True)
    print(f"  Saved: {tweet['filename']}")
    return out


if __name__ == "__main__":
    print(f"Generating {len(TWEETS)} statement cards → {OUTPUT_DIR}")
    for t in TWEETS:
        make_card(t)
    print("Done.")
