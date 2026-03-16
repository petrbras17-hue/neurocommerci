"""
MediaPipeline — CLI-first media processing pipeline.

Vels insight: CLI tools (yt-dlp, ffmpeg, whisper) consume ZERO tokens
until called, unlike MCP servers that constantly load tool descriptions
into the context window.

Pipeline: download → extract audio → transcribe → analyze
All steps are executed via asyncio.create_subprocess_exec calls to
local CLI tools.  No external SDK is imported at module load time.

Supported sources:
- YouTube (any public URL)
- Telegram public channels (via yt-dlp t.me handler)
- Instagram Reels / TikTok / VK (any yt-dlp supported host)
- Local audio/video files (voice messages, round videos from Telegram exports)

Usage:
    pipeline = MediaPipeline()
    result = await pipeline.full_pipeline("https://youtu.be/xyz")
    print(result.transcript)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TMP_DIR = "/tmp/neuro_media"
DOWNLOAD_TIMEOUT = 300   # seconds
EXTRACT_TIMEOUT = 120    # seconds
TRANSCRIBE_TIMEOUT = 600 # seconds
FFPROBE_TIMEOUT = 30     # seconds

# Audio/video file extensions recognised for direct transcription
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac", ".m4a", ".weba"}
_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".ts", ".flv", ".3gp"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MediaResult:
    """Result of a completed media pipeline run."""

    source_url: str
    video_path: Optional[str]
    audio_path: Optional[str]
    transcript: str
    language: str
    duration_seconds: Optional[float]
    processed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_dir(path: str) -> None:
    """Create directory tree if it does not exist."""
    os.makedirs(path, exist_ok=True)


def _stem(path: str) -> str:
    """Return filename without extension."""
    return Path(path).stem


async def _run(
    *args: str,
    timeout: int,
    label: str,
) -> tuple:
    """
    Run a subprocess asynchronously using create_subprocess_exec (no shell).

    Security note: create_subprocess_exec does NOT invoke a shell, so each
    element of *args* is passed verbatim to the OS — no shell injection risk.

    Returns (returncode, stdout, stderr).
    Raises asyncio.TimeoutError if *timeout* seconds elapse.
    """
    log.debug("[media_pipeline] %s: %s", label, " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.error("[media_pipeline] %s timed out after %ds", label, timeout)
        raise

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    return proc.returncode, stdout, stderr


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class MediaPipeline:
    """
    CLI-first media processing pipeline.

    All heavy lifting is delegated to yt-dlp, ffmpeg, and whisper.
    These tools consume zero tokens until explicitly invoked — unlike
    MCP servers that constantly load tool descriptions into the LLM
    context window.
    """

    def __init__(self, tmp_dir: str = DEFAULT_TMP_DIR) -> None:
        self.tmp_dir = tmp_dir
        _ensure_dir(tmp_dir)

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    async def check_dependencies(self) -> dict:
        """
        Verify that yt-dlp, ffmpeg, and whisper are installed.

        Returns a dict of the form::

            {
                "yt-dlp":  "2024.11.18",
                "ffmpeg":  "ffmpeg version 6.1",
                "whisper": "<help text first line>",
            }

        Missing tools have ``None`` as their value; install instructions
        are logged at WARNING level.
        """
        results: dict = {}

        checks = [
            ("yt-dlp",  ["yt-dlp",  "--version"], "pip install yt-dlp"),
            ("ffmpeg",  ["ffmpeg",  "-version"],   "brew install ffmpeg  # or: apt install ffmpeg"),
            ("whisper", ["whisper", "--help"],      "pip install openai-whisper"),
        ]

        for name, cmd, install_hint in checks:
            try:
                rc, stdout, stderr = await _run(
                    *cmd, timeout=15, label=f"check:{name}"
                )
                combined = (stdout or stderr)
                first_line = combined.splitlines()[0] if combined else ""
                results[name] = first_line if rc == 0 else None
                if rc != 0:
                    log.warning(
                        "[media_pipeline] %s not found or returned error. Install: %s",
                        name, install_hint,
                    )
            except FileNotFoundError:
                results[name] = None
                log.warning(
                    "[media_pipeline] %s not found in PATH. Install: %s",
                    name, install_hint,
                )
            except asyncio.TimeoutError:
                results[name] = None
                log.warning("[media_pipeline] %s check timed out.", name)

        return results

    # ------------------------------------------------------------------
    # Step 1: Download
    # ------------------------------------------------------------------

    async def download_video(
        self,
        url: str,
        output_dir: Optional[str] = None,
    ) -> str:
        """
        Download a video from *url* using yt-dlp.

        Quality cap: best available up to 720p to keep file sizes
        manageable for transcription purposes.

        Returns the absolute path to the downloaded file.
        Raises RuntimeError if yt-dlp exits with a non-zero code.
        """
        out_dir = output_dir or self.tmp_dir
        _ensure_dir(out_dir)

        template = os.path.join(out_dir, "%(id)s.%(ext)s")

        rc, stdout, stderr = await _run(
            "yt-dlp",
            "-f", "best[height<=720]/best",
            "--no-playlist",
            "--print", "after_move:filepath",
            "-o", template,
            url,
            timeout=DOWNLOAD_TIMEOUT,
            label="yt-dlp:download",
        )

        if rc != 0:
            log.error("[media_pipeline] yt-dlp failed (rc=%d): %s", rc, stderr[:500])
            raise RuntimeError(
                f"yt-dlp exited with code {rc}.\nstderr: {stderr[:500]}"
            )

        downloaded_path = stdout.strip()
        if not downloaded_path or not os.path.exists(downloaded_path):
            # Fallback: find the most recently modified file in the output dir
            all_files = sorted(
                Path(out_dir).iterdir(),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not all_files:
                raise RuntimeError("yt-dlp reported success but no output file found.")
            downloaded_path = str(all_files[0])

        log.info("[media_pipeline] Downloaded → %s", downloaded_path)
        return downloaded_path

    # ------------------------------------------------------------------
    # Step 2: Extract audio
    # ------------------------------------------------------------------

    async def extract_audio(self, video_path: str) -> str:
        """
        Extract audio track from *video_path* using ffmpeg.

        Output is an MP3 file with VBR quality 4 (approx 165 kbps).
        Returns the path to the resulting .mp3 file.
        """
        audio_path = os.path.join(
            self.tmp_dir,
            f"{_stem(video_path)}_audio.mp3",
        )

        rc, _stdout, stderr = await _run(
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-q:a", "4",
            audio_path,
            timeout=EXTRACT_TIMEOUT,
            label="ffmpeg:extract_audio",
        )

        if rc != 0:
            log.error("[media_pipeline] ffmpeg failed (rc=%d): %s", rc, stderr[:500])
            raise RuntimeError(
                f"ffmpeg exited with code {rc}.\nstderr: {stderr[:500]}"
            )

        log.info("[media_pipeline] Audio extracted → %s", audio_path)
        return audio_path

    # ------------------------------------------------------------------
    # Step 3: Transcribe
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_path: str,
        language: str = "ru",
        model: str = "base",
    ) -> str:
        """
        Transcribe *audio_path* using OpenAI Whisper CLI.

        Model selection guide:
        - ``tiny``   — fastest, lower accuracy (approx 1 GB RAM)
        - ``base``   — good default (default)
        - ``small``  — better accuracy, 2-3x slower
        - ``medium`` — best quality, 4-5x slower

        Returns the transcript as a plain string.
        """
        output_dir = self.tmp_dir

        rc, _stdout, stderr = await _run(
            "whisper",
            audio_path,
            "--language", language,
            "--model", model,
            "--output_format", "txt",
            "--output_dir", output_dir,
            timeout=TRANSCRIBE_TIMEOUT,
            label="whisper:transcribe",
        )

        if rc != 0:
            log.error("[media_pipeline] whisper failed (rc=%d): %s", rc, stderr[:500])
            raise RuntimeError(
                f"whisper exited with code {rc}.\nstderr: {stderr[:500]}"
            )

        # Whisper names the output file after the audio file stem
        txt_path = os.path.join(output_dir, f"{_stem(audio_path)}.txt")
        if not os.path.exists(txt_path):
            for candidate in sorted(Path(output_dir).glob(f"{Path(audio_path).stem}*.txt")):
                txt_path = str(candidate)
                break
            else:
                raise RuntimeError(
                    f"whisper finished but transcript file not found. "
                    f"Searched in: {output_dir}"
                )

        transcript = Path(txt_path).read_text(encoding="utf-8").strip()
        log.info(
            "[media_pipeline] Transcription complete (%d chars).",
            len(transcript),
        )
        return transcript

    # ------------------------------------------------------------------
    # Helper: video duration
    # ------------------------------------------------------------------

    async def get_video_duration(self, path: str) -> Optional[float]:
        """
        Return duration of a media file in seconds using ffprobe.

        Returns ``None`` if ffprobe is unavailable or the file has no
        duration entry.
        """
        try:
            rc, stdout, _stderr = await _run(
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
                timeout=FFPROBE_TIMEOUT,
                label="ffprobe:duration",
            )
        except (FileNotFoundError, asyncio.TimeoutError):
            return None

        if rc != 0 or not stdout:
            return None

        try:
            return float(stdout.strip())
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    async def full_pipeline(
        self,
        url: str,
        language: str = "ru",
        model: str = "base",
        cleanup: bool = True,
    ) -> MediaResult:
        """
        Run the complete pipeline: download → extract_audio → transcribe.

        Intermediate files are removed by default when *cleanup* is True.
        Returns a :class:`MediaResult` with the transcript and metadata.
        """
        video_path: Optional[str] = None
        audio_path: Optional[str] = None

        try:
            video_path = await self.download_video(url)
            duration = await self.get_video_duration(video_path)
            audio_path = await self.extract_audio(video_path)
            transcript = await self.transcribe(audio_path, language=language, model=model)

            return MediaResult(
                source_url=url,
                video_path=video_path,
                audio_path=audio_path,
                transcript=transcript,
                language=language,
                duration_seconds=duration,
            )
        finally:
            if cleanup:
                for p in [video_path, audio_path]:
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError as exc:
                            log.debug("[media_pipeline] cleanup failed for %s: %s", p, exc)
                # Remove whisper .txt output
                if audio_path:
                    txt = os.path.join(self.tmp_dir, f"{_stem(audio_path)}.txt")
                    if os.path.exists(txt):
                        try:
                            os.remove(txt)
                        except OSError:
                            pass

    # ------------------------------------------------------------------
    # Transcribe local file
    # ------------------------------------------------------------------

    async def transcribe_local_file(
        self,
        file_path: str,
        language: str = "ru",
        model: str = "base",
    ) -> str:
        """
        Transcribe a local audio or video file.

        Automatically detects whether the file is a video and extracts
        audio first if needed.  Suitable for Telegram voice messages
        (.ogg / .opus) and round videos (.mp4).
        """
        suffix = Path(file_path).suffix.lower()

        if suffix in _VIDEO_EXTS:
            log.info("[media_pipeline] Local file is video — extracting audio first.")
            audio_path = await self.extract_audio(file_path)
            try:
                return await self.transcribe(audio_path, language=language, model=model)
            finally:
                if os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except OSError:
                        pass
        elif suffix in _AUDIO_EXTS:
            return await self.transcribe(file_path, language=language, model=model)
        else:
            log.warning(
                "[media_pipeline] Unknown extension '%s', attempting direct transcription.",
                suffix,
            )
            return await self.transcribe(file_path, language=language, model=model)

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    async def batch_transcribe(
        self,
        urls: List[str],
        language: str = "ru",
        model: str = "base",
    ) -> List[MediaResult]:
        """
        Process a list of URLs sequentially, logging progress.

        Errors on individual URLs are caught and stored as ``[ERROR: ...]``
        transcripts so the rest of the batch can continue.
        """
        results: List[MediaResult] = []
        total = len(urls)

        for idx, url in enumerate(urls, start=1):
            log.info("[media_pipeline] Batch %d/%d: %s", idx, total, url)
            try:
                result = await self.full_pipeline(url, language=language, model=model)
                results.append(result)
            except Exception as exc:
                log.error(
                    "[media_pipeline] Batch item %d failed: %s — %s",
                    idx, url, exc,
                )
                results.append(
                    MediaResult(
                        source_url=url,
                        video_path=None,
                        audio_path=None,
                        transcript=f"[ERROR: {exc}]",
                        language=language,
                        duration_seconds=None,
                    )
                )

        succeeded = sum(
            1 for r in results if not r.transcript.startswith("[ERROR:")
        )
        log.info(
            "[media_pipeline] Batch complete: %d/%d succeeded.",
            succeeded, total,
        )
        return results
