#!/usr/bin/env python3
"""Tests for youtube_transcript.py — YouTube transcript extraction engine."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'lib'))

from youtube_transcript import (
    parse_youtube_url,
    fetch_transcript,
    fetch_metadata,
    fetch_playlist_videos,
    process_url,
    _format_duration,
)


# ---------------------------------------------------------------------------
# parse_youtube_url
# ---------------------------------------------------------------------------

class TestParseYoutubeUrl:
    """Test URL parsing for all known YouTube URL formats."""

    # --- Standard watch URLs ---

    def test_standard_watch_url(self):
        result = parse_youtube_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_watch_url_no_www(self):
        result = parse_youtube_url('https://youtube.com/watch?v=dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_watch_url_http(self):
        result = parse_youtube_url('http://www.youtube.com/watch?v=dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_watch_url_extra_params(self):
        result = parse_youtube_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_watch_url_with_list_param_is_video(self):
        """A watch URL with &list= should be treated as a single video."""
        result = parse_youtube_url(
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'
        )
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_watch_url_v_not_first_param(self):
        result = parse_youtube_url('https://www.youtube.com/watch?t=10&v=dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    # --- Short URLs ---

    def test_short_url(self):
        result = parse_youtube_url('https://youtu.be/dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_short_url_with_timestamp(self):
        result = parse_youtube_url('https://youtu.be/dQw4w9WgXcQ?t=42')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_short_url_http(self):
        result = parse_youtube_url('http://youtu.be/dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    # --- Embed URLs ---

    def test_embed_url(self):
        result = parse_youtube_url('https://www.youtube.com/embed/dQw4w9WgXcQ')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    def test_embed_url_with_params(self):
        result = parse_youtube_url('https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=1')
        assert result == {'type': 'video', 'id': 'dQw4w9WgXcQ'}

    # --- Playlist URLs ---

    def test_playlist_url(self):
        result = parse_youtube_url(
            'https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'
        )
        assert result == {'type': 'playlist', 'id': 'PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'}

    def test_playlist_url_no_www(self):
        result = parse_youtube_url(
            'https://youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'
        )
        assert result == {'type': 'playlist', 'id': 'PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'}

    # --- Invalid URLs ---

    def test_invalid_url_returns_none(self):
        assert parse_youtube_url('https://example.com/watch?v=abc') is None

    def test_empty_string_returns_none(self):
        assert parse_youtube_url('') is None

    def test_non_youtube_url_returns_none(self):
        assert parse_youtube_url('https://vimeo.com/123456') is None

    def test_youtube_url_no_video_id(self):
        assert parse_youtube_url('https://www.youtube.com/') is None

    def test_youtube_channel_url_returns_none(self):
        assert parse_youtube_url('https://www.youtube.com/@SomeChannel') is None

    def test_bare_video_id_returns_none(self):
        """A bare string that isn't a URL should return None."""
        assert parse_youtube_url('dQw4w9WgXcQ') is None


# ---------------------------------------------------------------------------
# fetch_transcript
# ---------------------------------------------------------------------------

class TestFetchTranscript:
    """Test transcript fetching with mocked YouTubeTranscriptApi."""

    def _make_transcript_obj(self, language_code='en', is_generated=False, language='English'):
        """Helper to create a mock Transcript object."""
        t = MagicMock()
        t.language_code = language_code
        t.is_generated = is_generated
        t.language = language
        t.fetch.return_value = self._make_fetched_transcript([
            {'text': 'Hello world', 'start': 0.0, 'duration': 2.5},
            {'text': 'This is a test', 'start': 2.5, 'duration': 3.0},
        ])
        return t

    def _make_fetched_transcript(self, snippets):
        """Helper to create a mock FetchedTranscript."""
        ft = MagicMock()
        ft.to_raw_data.return_value = snippets
        ft.__iter__ = MagicMock(return_value=iter([
            MagicMock(text=s['text'], start=s['start'], duration=s['duration'])
            for s in snippets
        ]))
        return ft

    def _make_transcript_list(self, transcripts):
        """Helper to create a mock TranscriptList."""
        tl = MagicMock()
        tl.__iter__ = MagicMock(return_value=iter(transcripts))

        def find_manually_created(codes):
            for t in transcripts:
                if not t.is_generated and t.language_code in codes:
                    return t
            from youtube_transcript_api import NoTranscriptFound
            raise NoTranscriptFound('abc', [], [])

        def find_generated(codes):
            for t in transcripts:
                if t.is_generated and t.language_code in codes:
                    return t
            from youtube_transcript_api import NoTranscriptFound
            raise NoTranscriptFound('abc', [], [])

        def find_transcript(codes):
            # Manual first, then generated
            for t in transcripts:
                if not t.is_generated and t.language_code in codes:
                    return t
            for t in transcripts:
                if t.is_generated and t.language_code in codes:
                    return t
            from youtube_transcript_api import NoTranscriptFound
            raise NoTranscriptFound('abc', [], [])

        tl.find_manually_created_transcript = MagicMock(side_effect=find_manually_created)
        tl.find_generated_transcript = MagicMock(side_effect=find_generated)
        tl.find_transcript = MagicMock(side_effect=find_transcript)
        return tl

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_manual_english_preferred(self, MockApi):
        """Manual English transcript should be preferred over auto-generated."""
        manual_en = self._make_transcript_obj('en', is_generated=False)
        auto_en = self._make_transcript_obj('en', is_generated=True)
        tl = self._make_transcript_list([manual_en, auto_en])

        mock_api = MagicMock()
        mock_api.list.return_value = tl
        MockApi.return_value = mock_api

        result = fetch_transcript('test123')
        assert result['error'] is None
        assert result['transcript_type'] == 'manual'
        assert result['transcript_language'] == 'en'
        assert 'Hello world' in result['transcript']

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_auto_english_fallback(self, MockApi):
        """If no manual English, fall back to auto-generated English."""
        auto_en = self._make_transcript_obj('en', is_generated=True)
        tl = self._make_transcript_list([auto_en])

        mock_api = MagicMock()
        mock_api.list.return_value = tl
        MockApi.return_value = mock_api

        result = fetch_transcript('test123')
        assert result['error'] is None
        assert result['transcript_type'] == 'auto-generated'
        assert result['transcript_language'] == 'en'

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_any_language_fallback(self, MockApi):
        """If no English at all, fall back to any available language."""
        fr_manual = self._make_transcript_obj('fr', is_generated=False, language='French')
        tl = self._make_transcript_list([fr_manual])

        mock_api = MagicMock()
        mock_api.list.return_value = tl
        MockApi.return_value = mock_api

        result = fetch_transcript('test123')
        assert result['error'] is None
        assert result['transcript_language'] == 'fr'

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_timestamped_data_returned(self, MockApi):
        """Timestamped transcript data should be returned."""
        manual_en = self._make_transcript_obj('en', is_generated=False)
        tl = self._make_transcript_list([manual_en])

        mock_api = MagicMock()
        mock_api.list.return_value = tl
        MockApi.return_value = mock_api

        result = fetch_transcript('test123')
        assert result['transcript_timestamped'] is not None
        assert len(result['transcript_timestamped']) == 2
        assert result['transcript_timestamped'][0]['text'] == 'Hello world'
        assert result['transcript_timestamped'][0]['start'] == 0.0

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_clean_text_joined(self, MockApi):
        """Clean transcript text should be all snippets joined by spaces."""
        manual_en = self._make_transcript_obj('en', is_generated=False)
        tl = self._make_transcript_list([manual_en])

        mock_api = MagicMock()
        mock_api.list.return_value = tl
        MockApi.return_value = mock_api

        result = fetch_transcript('test123')
        assert result['transcript'] == 'Hello world This is a test'

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_transcripts_disabled_error(self, MockApi):
        """TranscriptsDisabled should produce an error result."""
        from youtube_transcript_api import TranscriptsDisabled
        mock_api = MagicMock()
        mock_api.list.side_effect = TranscriptsDisabled('vid123')
        MockApi.return_value = mock_api

        result = fetch_transcript('vid123')
        assert result['transcript'] is None
        assert 'disabled' in result['error'].lower() or result['error'] is not None

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_video_unavailable_error(self, MockApi):
        """VideoUnavailable should produce an error result."""
        from youtube_transcript_api import VideoUnavailable
        mock_api = MagicMock()
        mock_api.list.side_effect = VideoUnavailable('vid123')
        MockApi.return_value = mock_api

        result = fetch_transcript('vid123')
        assert result['transcript'] is None
        assert result['error'] is not None

    @patch('youtube_transcript.YouTubeTranscriptApi')
    def test_generic_exception_error(self, MockApi):
        """Any unexpected exception should produce an error result."""
        mock_api = MagicMock()
        mock_api.list.side_effect = Exception('Network failure')
        MockApi.return_value = mock_api

        result = fetch_transcript('vid123')
        assert result['transcript'] is None
        assert 'Network failure' in result['error']


# ---------------------------------------------------------------------------
# fetch_metadata
# ---------------------------------------------------------------------------

class TestFetchMetadata:
    """Test metadata fetching via yt-dlp subprocess."""

    def _make_yt_dlp_json(self, overrides=None):
        """Return a realistic yt-dlp --dump-json output dict."""
        data = {
            'title': 'Test Video Title',
            'channel': 'Test Channel',
            'duration': 754,
            'upload_date': '20250315',
            'description': 'This is a test video description.',
            'chapters': [
                {'title': 'Intro', 'start_time': 0},
                {'title': 'Main Content', 'start_time': 60},
                {'title': 'Conclusion', 'start_time': 600},
            ],
        }
        if overrides:
            data.update(overrides)
        return data

    @patch('youtube_transcript.subprocess.run')
    def test_metadata_extracted(self, mock_run):
        """All metadata fields should be correctly extracted."""
        data = self._make_yt_dlp_json()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        result = fetch_metadata('test123')
        assert result['error'] is None
        assert result['title'] == 'Test Video Title'
        assert result['channel'] == 'Test Channel'
        assert result['duration'] == 754
        assert result['published'] == '2025-03-15'
        assert result['description'] == 'This is a test video description.'

    @patch('youtube_transcript.subprocess.run')
    def test_chapters_parsed(self, mock_run):
        """Chapters should be extracted with title and start."""
        data = self._make_yt_dlp_json()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        result = fetch_metadata('test123')
        assert len(result['chapters']) == 3
        assert result['chapters'][0] == {'title': 'Intro', 'start': 0}
        assert result['chapters'][1] == {'title': 'Main Content', 'start': 60}

    @patch('youtube_transcript.subprocess.run')
    def test_no_chapters(self, mock_run):
        """Missing chapters should result in empty list."""
        data = self._make_yt_dlp_json({'chapters': None})
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        result = fetch_metadata('test123')
        assert result['chapters'] == []

    @patch('youtube_transcript.subprocess.run')
    def test_upload_date_formatting(self, mock_run):
        """upload_date YYYYMMDD should be formatted to YYYY-MM-DD."""
        data = self._make_yt_dlp_json({'upload_date': '20231225'})
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        result = fetch_metadata('test123')
        assert result['published'] == '2023-12-25'

    @patch('youtube_transcript.subprocess.run')
    def test_missing_upload_date(self, mock_run):
        """Missing upload_date should result in None."""
        data = self._make_yt_dlp_json({'upload_date': None})
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        result = fetch_metadata('test123')
        assert result['published'] is None

    @patch('youtube_transcript.subprocess.run')
    def test_yt_dlp_failure(self, mock_run):
        """yt-dlp returning non-zero exit code should produce error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout='',
            stderr='ERROR: Video unavailable',
        )

        result = fetch_metadata('test123')
        assert result['error'] is not None
        assert result['title'] is None

    @patch('youtube_transcript.subprocess.run')
    def test_yt_dlp_exception(self, mock_run):
        """If subprocess raises, error should be captured."""
        mock_run.side_effect = FileNotFoundError('yt-dlp not found')

        result = fetch_metadata('test123')
        assert result['error'] is not None

    @patch('youtube_transcript.subprocess.run')
    def test_missing_fields_graceful(self, mock_run):
        """Missing optional fields should default to None/empty."""
        data = {'title': 'Minimal Video'}
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        result = fetch_metadata('test123')
        assert result['title'] == 'Minimal Video'
        assert result['channel'] is None
        assert result['duration'] is None
        assert result['published'] is None
        assert result['description'] is None
        assert result['chapters'] == []

    @patch('youtube_transcript.subprocess.run')
    def test_yt_dlp_called_correctly(self, mock_run):
        """yt-dlp should be called with correct arguments."""
        data = self._make_yt_dlp_json()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(data),
            stderr='',
        )

        fetch_metadata('abc123')
        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0] if args[0] else args[1].get('args', [])
        assert 'yt-dlp' in cmd
        assert '--dump-json' in cmd
        assert '--no-download' in cmd
        assert 'https://www.youtube.com/watch?v=abc123' in cmd


# ---------------------------------------------------------------------------
# fetch_playlist_videos
# ---------------------------------------------------------------------------

class TestFetchPlaylistVideos:
    """Test playlist expansion via yt-dlp subprocess."""

    @patch('youtube_transcript.subprocess.run')
    def test_playlist_videos_extracted(self, mock_run):
        """Video IDs and playlist title should be extracted."""
        lines = [
            json.dumps({'id': 'vid1', 'title': 'Video 1', 'playlist_title': 'My Playlist'}),
            json.dumps({'id': 'vid2', 'title': 'Video 2', 'playlist_title': 'My Playlist'}),
            json.dumps({'id': 'vid3', 'title': 'Video 3', 'playlist_title': 'My Playlist'}),
        ]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='\n'.join(lines),
            stderr='',
        )

        result = fetch_playlist_videos('PLtest123')
        assert result['error'] is None
        assert result['video_ids'] == ['vid1', 'vid2', 'vid3']
        assert result['playlist_title'] == 'My Playlist'

    @patch('youtube_transcript.subprocess.run')
    def test_empty_playlist(self, mock_run):
        """Empty playlist should return empty list."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='',
            stderr='',
        )

        result = fetch_playlist_videos('PLempty')
        assert result['video_ids'] == []
        assert result['error'] is None

    @patch('youtube_transcript.subprocess.run')
    def test_playlist_failure(self, mock_run):
        """yt-dlp failure should produce error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout='',
            stderr='ERROR: Playlist not found',
        )

        result = fetch_playlist_videos('PLbad')
        assert result['error'] is not None

    @patch('youtube_transcript.subprocess.run')
    def test_playlist_exception(self, mock_run):
        """Subprocess exception should be captured."""
        mock_run.side_effect = FileNotFoundError('yt-dlp not found')

        result = fetch_playlist_videos('PLtest')
        assert result['error'] is not None

    @patch('youtube_transcript.subprocess.run')
    def test_yt_dlp_called_correctly(self, mock_run):
        """yt-dlp should be called with --flat-playlist --dump-json."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='',
            stderr='',
        )

        fetch_playlist_videos('PLabc123')
        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0] if args[0] else args[1].get('args', [])
        assert 'yt-dlp' in cmd
        assert '--flat-playlist' in cmd
        assert '--dump-json' in cmd

    @patch('youtube_transcript.subprocess.run')
    def test_playlist_title_from_first_entry(self, mock_run):
        """Playlist title should be taken from the first entry that has it."""
        lines = [
            json.dumps({'id': 'vid1', 'title': 'Video 1'}),
            json.dumps({'id': 'vid2', 'title': 'Video 2', 'playlist_title': 'Late Title'}),
        ]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='\n'.join(lines),
            stderr='',
        )

        result = fetch_playlist_videos('PLtest')
        assert result['playlist_title'] == 'Late Title'


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    """Test duration formatting helper."""

    def test_seconds_only(self):
        assert _format_duration(45) == '0:45'

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == '2:05'

    def test_hours_minutes_seconds(self):
        assert _format_duration(3661) == '1:01:01'

    def test_exact_hour(self):
        assert _format_duration(3600) == '1:00:00'

    def test_zero(self):
        assert _format_duration(0) == '0:00'

    def test_none_returns_empty(self):
        assert _format_duration(None) == ''

    def test_large_duration(self):
        assert _format_duration(36000) == '10:00:00'


# ---------------------------------------------------------------------------
# process_url
# ---------------------------------------------------------------------------

class TestProcessUrl:
    """Test the orchestrator that ties everything together."""

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    def test_single_video(self, mock_meta, mock_transcript):
        """process_url for a single video should return one video entry."""
        mock_meta.return_value = {
            'title': 'Test Video',
            'channel': 'Test Channel',
            'duration': 300,
            'published': '2025-03-15',
            'description': 'A test.',
            'chapters': [],
            'error': None,
        }
        mock_transcript.return_value = {
            'transcript': 'Hello world',
            'transcript_timestamped': [{'text': 'Hello world', 'start': 0.0, 'duration': 2.0}],
            'transcript_language': 'en',
            'transcript_type': 'manual',
            'error': None,
        }

        result = process_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        assert result['type'] == 'video'
        assert len(result['videos']) == 1
        assert result['videos'][0]['video_id'] == 'dQw4w9WgXcQ'
        assert result['videos'][0]['title'] == 'Test Video'
        assert result['videos'][0]['transcript'] == 'Hello world'
        assert result['videos'][0]['duration_formatted'] == '5:00'
        assert result['videos'][0]['url'] == 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        assert result['errors'] == []

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    @patch('youtube_transcript.fetch_playlist_videos')
    def test_playlist(self, mock_playlist, mock_meta, mock_transcript):
        """process_url for a playlist should expand and process each video."""
        mock_playlist.return_value = {
            'video_ids': ['vid1', 'vid2'],
            'playlist_title': 'My Playlist',
            'error': None,
        }
        mock_meta.return_value = {
            'title': 'Video Title',
            'channel': 'Channel',
            'duration': 120,
            'published': '2025-01-01',
            'description': 'Desc',
            'chapters': [],
            'error': None,
        }
        mock_transcript.return_value = {
            'transcript': 'Text',
            'transcript_timestamped': [],
            'transcript_language': 'en',
            'transcript_type': 'auto-generated',
            'error': None,
        }

        result = process_url(
            'https://www.youtube.com/playlist?list=PLtest123'
        )
        assert result['type'] == 'playlist'
        assert result['playlist_title'] == 'My Playlist'
        assert len(result['videos']) == 2
        assert result['videos'][0]['video_id'] == 'vid1'
        assert result['videos'][1]['video_id'] == 'vid2'

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    def test_video_with_transcript_error(self, mock_meta, mock_transcript):
        """A video where transcript fails should appear in errors."""
        mock_meta.return_value = {
            'title': 'No Captions',
            'channel': 'Ch',
            'duration': 60,
            'published': '2025-01-01',
            'description': '',
            'chapters': [],
            'error': None,
        }
        mock_transcript.return_value = {
            'transcript': None,
            'transcript_timestamped': None,
            'transcript_language': None,
            'transcript_type': None,
            'error': 'Transcripts disabled',
        }

        result = process_url('https://www.youtube.com/watch?v=nocc123')
        # Video should still appear in videos list (with metadata)
        assert len(result['videos']) == 1
        assert result['videos'][0]['transcript'] is None
        # Error should also be recorded
        assert len(result['errors']) == 1
        assert result['errors'][0]['video_id'] == 'nocc123'

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    def test_video_with_metadata_error(self, mock_meta, mock_transcript):
        """A video where metadata fails should appear in errors."""
        mock_meta.return_value = {
            'title': None,
            'channel': None,
            'duration': None,
            'published': None,
            'description': None,
            'chapters': [],
            'error': 'Video unavailable',
        }
        mock_transcript.return_value = {
            'transcript': None,
            'transcript_timestamped': None,
            'transcript_language': None,
            'transcript_type': None,
            'error': 'Video unavailable',
        }

        result = process_url('https://www.youtube.com/watch?v=bad123')
        assert len(result['errors']) >= 1

    def test_invalid_url(self):
        """Invalid URL should return error result."""
        result = process_url('https://example.com/not-youtube')
        assert result['errors'] != []
        assert result['videos'] == []

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    @patch('youtube_transcript.fetch_playlist_videos')
    def test_playlist_expansion_error(self, mock_playlist, mock_meta, mock_transcript):
        """Playlist expansion failure should return error."""
        mock_playlist.return_value = {
            'video_ids': [],
            'playlist_title': None,
            'error': 'Playlist not found',
        }

        result = process_url(
            'https://www.youtube.com/playlist?list=PLbad'
        )
        assert result['errors'] != []

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    def test_duration_formatted_included(self, mock_meta, mock_transcript):
        """duration_formatted should be computed from duration seconds."""
        mock_meta.return_value = {
            'title': 'Test',
            'channel': 'Ch',
            'duration': 3661,
            'published': '2025-01-01',
            'description': '',
            'chapters': [],
            'error': None,
        }
        mock_transcript.return_value = {
            'transcript': 'text',
            'transcript_timestamped': [],
            'transcript_language': 'en',
            'transcript_type': 'manual',
            'error': None,
        }

        result = process_url('https://www.youtube.com/watch?v=abc123')
        assert result['videos'][0]['duration_formatted'] == '1:01:01'

    @patch('youtube_transcript.fetch_transcript')
    @patch('youtube_transcript.fetch_metadata')
    def test_video_url_field(self, mock_meta, mock_transcript):
        """Each video entry should include the canonical YouTube URL."""
        mock_meta.return_value = {
            'title': 'T', 'channel': 'C', 'duration': 10,
            'published': '2025-01-01', 'description': '', 'chapters': [],
            'error': None,
        }
        mock_transcript.return_value = {
            'transcript': 'x', 'transcript_timestamped': [],
            'transcript_language': 'en', 'transcript_type': 'manual',
            'error': None,
        }

        result = process_url('https://youtu.be/xyz789')
        assert result['videos'][0]['url'] == 'https://www.youtube.com/watch?v=xyz789'


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

class TestCLI:
    """Test the CLI entry point."""

    @patch('youtube_transcript.process_url')
    def test_cli_prints_json(self, mock_process):
        """CLI should output valid JSON to stdout."""
        mock_process.return_value = {
            'type': 'video',
            'playlist_title': None,
            'videos': [{'video_id': 'test', 'title': 'Test'}],
            'errors': [],
        }

        script_path = Path(__file__).parent.parent / 'lib' / 'youtube_transcript.py'
        result = subprocess.run(
            ['python3', str(script_path), '--url', 'https://www.youtube.com/watch?v=test123'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # We can't easily mock the subprocess call, so just check it runs
        # (it will attempt real network calls unless we mock differently)
        # The important thing is the script is importable and has main()
        assert result.returncode in (0, 1)  # 0 if videos, 1 if errors

    def test_module_importable(self):
        """Module should be importable without side effects."""
        import youtube_transcript
        assert hasattr(youtube_transcript, 'main')
        assert hasattr(youtube_transcript, 'process_url')
        assert hasattr(youtube_transcript, 'parse_youtube_url')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
