#!/usr/bin/env python3
"""Render an arbitrary script to a podcast audio file with Kokoro TTS.

speak_brief.py can only voice the day's journal briefing. This does the same
synthesis for any script — daily news, research summaries, anything written
ahead of time — without going near NotebookLM or a hosted model. Fully local.

Usage:
    python3 make_podcast.py script.txt --output out.wav
    python3 make_podcast.py script.txt --output out.wav --voice af_nova --speed 1.05
    python3 make_podcast.py --list-voices
"""
import argparse
import re
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).parent.parent / 'models'
DEFAULT_VOICE = 'af_heart'
DEFAULT_SPEED = 1.0
MAX_CHUNK = 400          # characters; Kokoro degrades on very long inputs


def load_kokoro():
    from kokoro_onnx import Kokoro
    model = MODELS_DIR / 'kokoro-v1.0.onnx'
    voices = MODELS_DIR / 'voices-v1.0.bin'
    for p in (model, voices):
        if not p.exists():
            sys.exit(f'missing model file: {p}')
    return Kokoro(str(model), str(voices))


def chunk_text(text):
    """Split on paragraphs, then sentences, keeping chunks under MAX_CHUNK."""
    chunks = []
    for para in [p.strip() for p in text.split('\n\n') if p.strip()]:
        if len(para) <= MAX_CHUNK:
            chunks.append(para)
            continue
        sentences, cur = re.split(r'(?<=[.!?])\s+', para), ''
        for s in sentences:
            if len(cur) + len(s) + 1 <= MAX_CHUNK:
                cur = f'{cur} {s}'.strip()
            else:
                if cur:
                    chunks.append(cur)
                cur = s
        if cur:
            chunks.append(cur)
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('script', nargs='?', help='path to the script text file')
    ap.add_argument('--output', help='output .wav path')
    ap.add_argument('--voice', default=DEFAULT_VOICE)
    ap.add_argument('--speed', type=float, default=DEFAULT_SPEED)
    ap.add_argument('--list-voices', action='store_true')
    args = ap.parse_args()

    kokoro = load_kokoro()

    if args.list_voices:
        print('\n'.join(sorted(kokoro.get_voices())))
        return 0

    if not args.script or not args.output:
        sys.exit('script and --output are both required')

    text = Path(args.script).read_text().strip()
    chunks = chunk_text(text)
    words = len(text.split())
    print(f'{words} words, {len(chunks)} chunks, voice={args.voice}, speed={args.speed}')

    import numpy as np
    import soundfile as sf

    pieces, sr = [], None
    for i, chunk in enumerate(chunks, 1):
        samples, sr = kokoro.create(chunk, voice=args.voice, speed=args.speed)
        pieces.append(samples)
        # A short pause between chunks so paragraphs do not run together.
        pieces.append(np.zeros(int(sr * 0.35), dtype=samples.dtype))
        if i % 10 == 0 or i == len(chunks):
            print(f'  {i}/{len(chunks)}')

    combined = np.concatenate(pieces)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), combined, sr)
    dur = len(combined) / sr
    print(f'wrote {out} — {dur:.0f}s ({dur/60:.1f} min), {out.stat().st_size/1e6:.1f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
