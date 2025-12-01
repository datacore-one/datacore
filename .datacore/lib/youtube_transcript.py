#!/usr/bin/env python3
"""
YouTube Transcript Extractor

Extracts transcripts and metadata from YouTube videos and playlists.
Uses youtube-transcript-api for captions and yt-dlp for metadata.

Usage:
    python youtube_transcript.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
    python youtube_transcript.py --url "https://www.youtube.com/playlist?list=PLAYLIST_ID"

Output: JSON to stdout with video metadata and transcripts.
Exit codes: 0 = success (videos found), 1 = failure (no videos or errors).
"""

import json
import subprocess
import sys
from typing import Optional, Dict, List, Any
from urllib.parse import urlparse, parse_qs

from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)


def parse_youtube_url(url: str) -> Optional[Dict[str, str]]:
    """Parse a YouTube URL and extract video or playlist ID.

    Returns:
        {"type": "video", "id": "..."} for video URLs
        {"type": "playlist", "id": "..."} for playlist URLs
        None for invalid/unrecognized URLs
    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Must have a scheme (http/https)
    if parsed.scheme not in ('http', 'https'):
        return None

    hostname = (parsed.hostname or '').lower()

    # youtu.be short URLs
    if hostname == 'youtu.be':
        video_id = parsed.path.lstrip('/')
        if video_id:
            return {'type': 'video', 'id': video_id}
        return None

    # youtube.com URLs
    if hostname not in ('youtube.com', 'www.youtube.com'):
        return None

    path = parsed.path
    qs = parse_qs(parsed.query)

    # /watch?v=VIDEO_ID (may also have &list= — treat as video)
    if path == '/watch':
        video_ids = qs.get('v', [])
        if video_ids:
            return {'type': 'video', 'id': video_ids[0]}
        return None

    # /embed/VIDEO_ID
    if path.startswith('/embed/'):
        video_id = path.split('/embed/')[1].split('/')[0].split('?')[0]
        if video_id:
            return {'type': 'video', 'id': video_id}
        return None

    # /playlist?list=PLAYLIST_ID
    if path == '/playlist':
        list_ids = qs.get('list', [])
        if list_ids:
            return {'type': 'playlist', 'id': list_ids[0]}
        return None

    return None


def fetch_transcript(video_id: str) -> Dict[str, Any]:
    """Fetch transcript for a YouTube video.

    Language preference cascade:
    1. Manual English transcript
    2. Auto-generated English transcript
    3. Any available transcript (first available)

    Returns dict with: transcript, transcript_timestamped,
    transcript_language, transcript_type, error
    """
    error_result = {
        'transcript': None,
        'transcript_timestamped': None,
        'transcript_language': None,
        'transcript_type': None,
        'error': None,
    }

    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # Build lists of available transcripts
        transcript_obj = None
        transcript_type = None

        # 1. Try manual English
        try:
            transcript_obj = transcript_list.find_manually_created_transcript(['en'])
            transcript_type = 'manual'
        except NoTranscriptFound:
            pass

        # 2. Try auto-generated English
        if transcript_obj is None:
            try:
                transcript_obj = transcript_list.find_generated_transcript(['en'])
                transcript_type = 'auto-generated'
            except NoTranscriptFound:
                pass

        # 3. Fall back to any available transcript
        if transcript_obj is None:
            for t in transcript_list:
                transcript_obj = t
                transcript_type = 'auto-generated' if t.is_generated else 'manual'
                break

        if transcript_obj is None:
            error_result['error'] = 'No transcripts available'
            return error_result

        # Fetch the transcript data
        fetched = transcript_obj.fetch()
        raw_data = fetched.to_raw_data()

        # Build clean text (join all snippet texts)
        clean_text = ' '.join(snippet['text'] for snippet in raw_data)

        return {
            'transcript': clean_text,
            'transcript_timestamped': raw_data,
            'transcript_language': transcript_obj.language_code,
            'transcript_type': transcript_type,
            'error': None,
        }

    except TranscriptsDisabled:
        error_result['error'] = 'Transcripts are disabled for this video'
        return error_result
    except VideoUnavailable:
        error_result['error'] = 'Video is unavailable'
        return error_result
    except NoTranscriptFound as e:
        error_result['error'] = f'No transcript found: {e}'
        return error_result
    except Exception as e:
        error_result['error'] = str(e)
        return error_result


def fetch_metadata(video_id: str) -> Dict[str, Any]:
    """Fetch video metadata using yt-dlp.

    Returns dict with: title, channel, duration, published,
    description, chapters, error
    """
    error_result = {
        'title': None,
        'channel': None,
        'duration': None,
        'published': None,
        'description': None,
        'chapters': [],
        'error': None,
    }

    try:
        url = f'https://www.youtube.com/watch?v={video_id}'
        result = subprocess.run(
            ['yt-dlp', '--dump-json', '--no-download', url],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error_result['error'] = result.stderr.strip() or f'yt-dlp exited with code {result.returncode}'
            return error_result

        data = json.loads(result.stdout)

        # Parse upload_date YYYYMMDD -> YYYY-MM-DD
        upload_date = data.get('upload_date')
        published = None
        if upload_date and len(upload_date) == 8:
            published = f'{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}'

        # Parse chapters
        chapters = []
        raw_chapters = data.get('chapters') or []
        for ch in raw_chapters:
            chapters.append({
                'title': ch.get('title', ''),
                'start': ch.get('start_time', 0),
            })

        return {
            'title': data.get('title'),
            'channel': data.get('channel'),
            'duration': data.get('duration'),
            'published': published,
            'description': data.get('description'),
            'chapters': chapters,
            'error': None,
        }

    except json.JSONDecodeError as e:
        error_result['error'] = f'Failed to parse yt-dlp output: {e}'
        return error_result
    except Exception as e:
        error_result['error'] = str(e)
        return error_result


def fetch_playlist_videos(playlist_id: str) -> Dict[str, Any]:
    """Fetch video IDs from a YouTube playlist using yt-dlp.

    Returns dict with: video_ids, playlist_title, error
    """
    error_result = {
        'video_ids': [],
        'playlist_title': None,
        'error': None,
    }

    try:
        url = f'https://www.youtube.com/playlist?list={playlist_id}'
        result = subprocess.run(
            ['yt-dlp', '--flat-playlist', '--dump-json', url],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            error_result['error'] = result.stderr.strip() or f'yt-dlp exited with code {result.returncode}'
            return error_result

        video_ids = []
        playlist_title = None

        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                vid_id = entry.get('id')
                if vid_id:
                    video_ids.append(vid_id)
                if playlist_title is None:
                    playlist_title = entry.get('playlist_title')
            except json.JSONDecodeError:
                continue

        return {
            'video_ids': video_ids,
            'playlist_title': playlist_title,
            'error': None,
        }

    except Exception as e:
        error_result['error'] = str(e)
        return error_result


def _format_duration(seconds: Optional[int]) -> str:
    """Format seconds into human-readable duration.

    Returns "H:MM:SS" for durations >= 1 hour, "M:SS" otherwise.
    Returns "" for None.
    """
    if seconds is None:
        return ''

    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    else:
        return f'{minutes}:{secs:02d}'


def process_url(url: str) -> Dict[str, Any]:
    """Process a YouTube URL (video or playlist).

    Orchestrates: parse URL -> expand playlist if needed ->
    fetch metadata + transcript for each video.

    Returns dict with: type, playlist_title, videos, errors
    """
    result = {
        'type': 'video',
        'playlist_title': None,
        'videos': [],
        'errors': [],
    }

    parsed = parse_youtube_url(url)
    if parsed is None:
        result['errors'].append({
            'video_id': None,
            'error': f'Could not parse YouTube URL: {url}',
        })
        return result

    # Determine list of video IDs to process
    video_ids = []

    if parsed['type'] == 'playlist':
        result['type'] = 'playlist'
        playlist_result = fetch_playlist_videos(parsed['id'])

        if playlist_result['error']:
            result['errors'].append({
                'video_id': None,
                'error': f'Playlist error: {playlist_result["error"]}',
            })
            return result

        video_ids = playlist_result['video_ids']
        result['playlist_title'] = playlist_result['playlist_title']

        if not video_ids:
            result['errors'].append({
                'video_id': None,
                'error': 'Playlist is empty or could not be expanded',
            })
            return result

    else:
        video_ids = [parsed['id']]

    # Process each video
    for vid_id in video_ids:
        metadata = fetch_metadata(vid_id)
        transcript = fetch_transcript(vid_id)

        video_entry = {
            'video_id': vid_id,
            'title': metadata.get('title'),
            'channel': metadata.get('channel'),
            'duration': metadata.get('duration'),
            'duration_formatted': _format_duration(metadata.get('duration')),
            'published': metadata.get('published'),
            'description': metadata.get('description'),
            'chapters': metadata.get('chapters', []),
            'transcript': transcript.get('transcript'),
            'transcript_timestamped': transcript.get('transcript_timestamped'),
            'transcript_language': transcript.get('transcript_language'),
            'transcript_type': transcript.get('transcript_type'),
            'url': f'https://www.youtube.com/watch?v={vid_id}',
        }

        result['videos'].append(video_entry)

        # Record errors
        if metadata.get('error'):
            result['errors'].append({
                'video_id': vid_id,
                'error': f'Metadata: {metadata["error"]}',
            })
        if transcript.get('error'):
            result['errors'].append({
                'video_id': vid_id,
                'error': f'Transcript: {transcript["error"]}',
            })

    return result


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Extract transcripts and metadata from YouTube videos/playlists.',
    )
    parser.add_argument(
        '--url',
        required=True,
        help='YouTube video or playlist URL',
    )

    args = parser.parse_args()
    result = process_url(args.url)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result['videos']:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
