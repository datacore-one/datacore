# Demo Video Pipeline — Screen Recording to X Post

Repeatable process for creating polished demo videos with styled captions
and publishing them to X. Established 2026-03-13 with the Fairdrop MCP demo.

## Trigger

Conversational: "create demo video", "publish demo video", "process screen recording"

## Prerequisites

- Screen recording file (MP4, any resolution)
- ffmpeg with libass support (`brew install homebrew-ffmpeg/ffmpeg/ffmpeg`)
- Late.dev API key in `.datacore/env/.env` as `LATE_API_KEY`
- CTA end card image (PNG)
- Optional: background music MP3

## Pipeline

### Phase 1: Prepare Source

1. **Verify source video**:
   ```bash
   ffprobe -v quiet -show_format -show_streams SOURCE.mp4
   ```
   Check: resolution, duration, audio tracks present/absent.

2. **Speed up if needed** (e.g., 2x):
   ```bash
   ffmpeg -y -i SOURCE.mp4 -filter:v "setpts=0.5*PTS" -an OUTPUT-2x.mp4
   ```
   Note: `-an` strips audio. If keeping audio at 2x, use `-filter:a "atempo=2.0"`.

3. **Trim to desired length**:
   ```bash
   ffmpeg -y -i INPUT.mp4 -t SECONDS -c copy OUTPUT-trimmed.mp4
   ```

### Phase 2: Write Captions

1. **Create SRT file** with timed captions:
   ```
   1
   00:00:02,000 --> 00:00:06,500
   First caption line.

   2
   00:00:07,500 --> 00:00:12,000
   Second caption line.
   ```

   Guidelines:
   - 1-2 short sentences per caption
   - 4-6 second display time
   - 1-2 second gaps between captions
   - Match captions to what's visible on screen

2. **Convert to ASS** for styled subtitles:
   ```
   [Script Info]
   Title: Demo Captions
   ScriptType: v4.00+
   PlayResX: TARGET_WIDTH
   PlayResY: TARGET_HEIGHT
   WrapStyle: 0

   [V4+ Styles]
   Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
   Style: Default,Arial,FONTSIZE,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,2,0,2,40,40,MARGINV,1
   ```

   Key ASS style parameters:
   - `BorderStyle=3` — opaque background box (not outline)
   - `BackColour=&HC0000000` — semi-transparent black (C0 = 75% opacity)
   - `Alignment=2` — bottom-center
   - `MarginV` — distance from bottom (130 works for Claude Desktop UI)
   - `Fontsize` — 34 works well at 1672x1080
   - `Bold=-1` — bold text

### Phase 3: Burn Subtitles

```bash
ffmpeg -y -i INPUT.mp4 -t DURATION \
  -vf "scale=WIDTH:HEIGHT,ass=CAPTIONS.ass" \
  -c:v libx264 -crf 24 -preset fast \
  -c:a aac -b:a 96k \
  OUTPUT-captioned.mp4
```

**Verify**: Take a screenshot or play back to check caption positioning.
If captions overlap UI elements, adjust MarginV in the ASS file and re-run.

### Phase 4: Add Background Music (if available)

```bash
ffmpeg -y -i VIDEO.mp4 -i MUSIC.mp3 \
  -filter_complex "[1:a]volume=0.15[bg];[0:a][bg]amix=inputs=2:duration=first[aout]" \
  -map 0:v -map "[aout]" \
  -c:v copy -c:a aac -b:a 128k \
  OUTPUT-with-music.mp4
```

If source video has NO audio track, use simpler command:
```bash
ffmpeg -y -i VIDEO.mp4 -i MUSIC.mp3 \
  -map 0:v -map 1:a \
  -c:v copy -c:a aac -b:a 128k \
  -shortest \
  OUTPUT-with-music.mp4
```

**Always verify audio tracks first**:
```bash
ffprobe -v quiet -show_streams INPUT.mp4 | grep codec_type
```

### Phase 5: Append CTA End Card

1. **Create CTA video from image**:
   ```bash
   ffmpeg -y -loop 1 -i CTA.png -t 5 \
     -vf "scale=WIDTH:HEIGHT:force_original_aspect_ratio=decrease,pad=WIDTH:HEIGHT:(ow-iw)/2:(oh-ih)/2:color=0D0D1A" \
     -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p \
     CTA-video.mp4
   ```

2. **Concatenate** (video-only if no audio):
   ```bash
   ffmpeg -y -i MAIN.mp4 -i CTA-video.mp4 \
     -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0[v]" \
     -map "[v]" -c:v libx264 -crf 26 -preset fast \
     FINAL.mp4
   ```

   With audio:
   ```bash
   ffmpeg -y -i MAIN.mp4 -i CTA-video.mp4 \
     -filter_complex "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]" \
     -map "[v]" -map "[a]" -c:v libx264 -crf 26 -preset fast -c:a aac \
     FINAL.mp4
   ```

### Phase 6: Verify Final Output

```bash
ffprobe -v quiet -show_format -show_streams FINAL.mp4
```

Check:
- Duration correct (main + CTA)
- Resolution matches target
- File size reasonable (<15MB for X)
- Audio track present (if expected)

### Phase 7: Publish to X via Late.dev

1. **Load API key**:
   ```bash
   LATE_API_KEY=$(grep LATE_API_KEY .datacore/env/.env | cut -d= -f2)
   ```

2. **Get upload token**:
   ```bash
   TOKEN=$(curl -s -X POST \
     -H "Authorization: Bearer $LATE_API_KEY" \
     "https://getlate.dev/api/v1/media/upload-token" \
     | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
   ```

3. **Upload video** (field name MUST be `files`, token as query param):
   ```bash
   MEDIA_URL=$(curl -s -X POST \
     -H "Authorization: Bearer $LATE_API_KEY" \
     -F "files=@FINAL.mp4;type=video/mp4" \
     "https://getlate.dev/api/v1/media/upload?token=$TOKEN" \
     | python3 -c "import json,sys; print(json.load(sys.stdin)['files'][0]['url'])")
   ```

4. **Create scheduled post** (schedule 2min in future for reliable publishing):
   ```bash
   SCHEDULE_TIME=$(date -u -v+2M '+%Y-%m-%dT%H:%M:%S.000Z')
   curl -s -X POST \
     -H "Authorization: Bearer $LATE_API_KEY" \
     -H "Content-Type: application/json" \
     -d "{
       \"accountIds\": [\"ACCOUNT_ID\"],
       \"text\": \"POST TEXT HERE\",
       \"mediaUrls\": [\"$MEDIA_URL\"],
       \"status\": \"scheduled\",
       \"scheduledFor\": \"$SCHEDULE_TIME\"
     }" \
     "https://getlate.dev/api/v1/posts"
   ```

   Known account IDs:
   - FairDataSociety: `69a1a943dc8cab9432aa82d6`

5. **Verify**: Wait 3 minutes, then check X timeline or Late.dev dashboard.

## Pitfalls & Lessons Learned

| Issue | Solution |
|-------|----------|
| Core homebrew ffmpeg lacks libass | Use `homebrew-ffmpeg/ffmpeg` tap |
| Captions overlap app UI | Increase MarginV (130+ for Claude Desktop) |
| No audio track in source | Use video-only concat filter, verify with ffprobe |
| Late.dev media URLs expire on post delete | Re-upload before creating replacement post |
| `status: "published"` unreliable | Use `status: "scheduled"` with future time |
| Browser automation for video editing | Don't. Use ffmpeg — programmatic and reproducible |
| Forgot background music | Always check audio tracks early in pipeline |

## File Naming Convention

```
PROJECT-demo-raw.mp4          # Original screen recording
PROJECT-demo-2x.mp4           # Sped up
PROJECT-demo-captions.srt     # Caption text
PROJECT-demo-captions.ass     # Styled captions
PROJECT-demo-trimmed.mp4      # Trimmed with captions burned
PROJECT-demo-final.mp4        # With CTA appended
PROJECT-cta-endcard.png       # CTA image
```

## Module

comms
