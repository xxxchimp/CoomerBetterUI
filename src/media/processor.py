from __future__ import annotations

import subprocess
import shutil
import math
import logging
import sys
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtGui import QImage
from PyQt6.QtCore import Qt, QSize


logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}


def _subprocess_kwargs() -> dict:
    """Get platform-specific subprocess kwargs to hide console windows on Windows."""
    kwargs = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


class MediaProcessor:
    """
    Pure media decoding utility.

    Responsibilities:
    - Load images
    - Extract video frames
    - Resize
    - Return QImage

    Non-responsibilities:
    - Caching
    - Threading
    - UI
    """
    _keyframe_lookahead_seconds = 5.0

    def __init__(self, enable_hwaccel: bool = True):
        """
        Args:
            enable_hwaccel: Enable GPU hardware acceleration for video decoding
        """
        self._enable_hwaccel = enable_hwaccel
        self._hwaccel_method: Optional[str] = None
        self._hwaccel_detected = False
        if enable_hwaccel:
            self._detect_hwaccel()
            if self._hwaccel_detected:
                logger.info(f"Hardware acceleration enabled: {self._hwaccel_method}")
            else:
                logger.info("Hardware acceleration requested but not available, using software decoding")
        else:
            logger.info("Hardware acceleration disabled")

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def generate_thumbnail(
        self,
        media: str | Path,
        size: Tuple[int, int] | QSize,
        timestamp: Optional[float] = None,
    ) -> QImage:
        """
        Entry point used by ThumbnailManager.

        Automatically dispatches based on file type.
        """
        path = Path(media)

        if not path.exists():
            raise FileNotFoundError(path)

        if path.suffix.lower() in VIDEO_EXTS:
            return self.generate_video_thumbnail(
                source=path,
                size=size,
                timestamp=timestamp or 0.0,
            )

        return self.generate_image_thumbnail(
            source=path,
            size=size,
        )

    # ------------------------------------------------------------
    # Image
    # ------------------------------------------------------------

    def generate_image_thumbnail(
        self,
        source: Path,
        size: Tuple[int, int] | QSize,
    ) -> QImage:
        img = QImage(str(source))
        if img.isNull():
            raise RuntimeError(f"Failed to load image: {source}")

        if isinstance(size, QSize):
            if size.width() <= 0 or size.height() <= 0:
                return img
        elif isinstance(size, tuple) and len(size) == 2:
            if size[0] <= 0 or size[1] <= 0:
                return img

        return self._resize(img, size)

    # ------------------------------------------------------------
    # Video
    # ------------------------------------------------------------

    def generate_video_thumbnail(
        self,
        source: Path,
        size: Tuple[int, int] | QSize,
        timestamp: float,
    ) -> QImage:
        if not self._ffmpeg_available():
            raise RuntimeError("ffmpeg not found on PATH")

        duration = None
        if self._ffprobe_available():
            duration = self._probe_duration(source)
        candidates = self._pick_candidate_timestamps(duration, timestamp)

        best_score = None
        best_ts = None
        score_size = self._normalize_size(size)
        max_dim = max(score_size)
        if max_dim > 256:
            scale = 256 / max_dim
            score_size = (max(1, int(score_size[0] * scale)), max(1, int(score_size[1] * scale)))

        for ts in candidates:
            frame = self._extract_video_frame(source, score_size, ts)
            if frame is None or frame.isNull():
                continue
            score = self._score_frame(frame)
            if best_score is None or score > best_score:
                best_score = score
                best_ts = ts

        if best_ts is None:
            best_ts = max(timestamp, 0.0)

        best_ts = self._align_to_keyframe(source, best_ts)
        img = self._extract_video_frame(source, size, best_ts)
        if img is None or img.isNull():
            raise RuntimeError(f"Failed to decode video frame: {source}")
        return img

    def generate_video_thumbnail_from_url(
        self,
        url: str,
        size: Tuple[int, int] | QSize,
        timestamp: float,
    ) -> QImage:
        if not self._ffmpeg_available():
            raise RuntimeError("ffmpeg not found on PATH")
        timestamp = self._align_to_keyframe(url, timestamp)
        img = self._extract_video_frame(url, size, timestamp)
        if img is None or img.isNull():
            raise RuntimeError(f"Failed to decode video frame: {url}")
        return img

    def generate_hls_thumbnail(
        self,
        url: str,
        size: Tuple[int, int] | QSize,
        timestamp: float,
    ) -> QImage:
        if not self._ffmpeg_available():
            raise RuntimeError("ffmpeg not found on PATH")
        
        # Build base command
        cmd = ["ffmpeg"]
        
        # Add hardware acceleration if available
        if self._enable_hwaccel and self._hwaccel_detected and self._hwaccel_method:
            cmd.extend(["-hwaccel", self._hwaccel_method])
        
        cmd.extend([
            "-loglevel", "error",
            "-ss", str(max(timestamp, 0.0)),
            "-i", url,
            "-frames:v", "1",
            "-vf", self._scale_filter(size),
            "-f", "image2pipe",
            "-vcodec", "png",
            "-",
        ])
        
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                **_subprocess_kwargs(),
            )
        except subprocess.CalledProcessError as e:
            # If hwaccel was used, try again without it
            if self._enable_hwaccel and self._hwaccel_detected:
                cmd = [
                    "ffmpeg",
                    "-loglevel", "error",
                    "-ss", str(max(timestamp, 0.0)),
                    "-i", url,
                    "-frames:v", "1",
                    "-vf", self._scale_filter(size),
                    "-f", "image2pipe",
                    "-vcodec", "png",
                    "-",
                ]
                try:
                    proc = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                        **_subprocess_kwargs(),
                    )
                except subprocess.CalledProcessError as e2:
                    raise RuntimeError(
                        f"ffmpeg failed for {url}: {e2.stderr.decode(errors='ignore')}"
                    )
            else:
                raise RuntimeError(
                    f"ffmpeg failed for {url}: {e.stderr.decode(errors='ignore')}"
                )
        
        img = QImage.fromData(proc.stdout, "PNG")
        if img.isNull():
            raise RuntimeError(f"Failed to decode HLS frame: {url}")
        return img

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _resize(self, img: QImage, size: Tuple[int, int] | QSize) -> QImage:
        w, h = self._normalize_size(size)
        return img.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _normalize_size(size: Tuple[int, int] | QSize) -> Tuple[int, int]:
        if isinstance(size, QSize):
            return (int(size.width()), int(size.height()))
        if isinstance(size, tuple) and len(size) == 2:
            return (int(size[0]), int(size[1]))
        raise ValueError(f"Invalid size: {size}")

    def _ffmpeg_available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def _ffprobe_available(self) -> bool:
        return shutil.which("ffprobe") is not None

    def _detect_hwaccel(self) -> None:
        """Detect available hardware acceleration methods."""
        if not self._ffmpeg_available():
            return

        # Check if we should avoid AMD iGPU
        if self._is_amd_igpu():
            return

        cmd = ["ffmpeg", "-hwaccels"]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                **_subprocess_kwargs(),
            )
        except subprocess.CalledProcessError:
            return

        hwaccels = [line.strip() for line in proc.stdout.splitlines()]
        
        # Priority order: cuda (NVIDIA) > d3d11va > dxva2 > vaapi > qsv > videotoolbox
        # Prioritize NVIDIA CUDA over generic Windows APIs to avoid AMD iGPU
        priority = ["cuda", "d3d11va", "dxva2", "vaapi", "qsv", "videotoolbox"]
        
        for method in priority:
            if method in hwaccels:
                self._hwaccel_method = method
                self._hwaccel_detected = True
                return

    def _is_amd_igpu(self) -> bool:
        """Check if system has AMD integrated GPU that should be avoided."""
        try:
            # Check GPU info on Windows using wmic
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                **_subprocess_kwargs(),
            )
            gpu_names = result.stdout.lower()
            
            # Check if NVIDIA dGPU is present
            has_nvidia = "nvidia" in gpu_names or "geforce" in gpu_names or "quadro" in gpu_names
            
            # Check if AMD GPU is present
            has_amd = "amd" in gpu_names or "radeon" in gpu_names
            
            # Check for AMD integrated GPUs specifically
            is_amd_igpu = any(igpu in gpu_names for igpu in [
                "vega", "renoir", "cezanne", "barcelo", "rembrandt", 
                "phoenix", "strix point", "radeon graphics"
            ])
            
            # Only disable hwaccel if:
            # 1. AMD iGPU is detected AND
            # 2. No NVIDIA dGPU is present (which would be preferred anyway)
            if is_amd_igpu and not has_nvidia:
                logger.info("AMD integrated GPU detected without discrete GPU - disabling hardware acceleration")
                return True
                
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            # If wmic fails, allow hwaccel to proceed
            pass
        
        return False

    def _probe_duration(self, source: Path) -> Optional[float]:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(source),
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                **_subprocess_kwargs(),
            )
        except subprocess.CalledProcessError:
            return None
        output = proc.stdout.strip()
        try:
            value = float(output)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    def _align_to_keyframe(self, source: Path | str, timestamp: float) -> float:
        if not self._ffprobe_available():
            return max(0.0, timestamp or 0.0)
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            ts = 0.0
        ts = max(0.0, ts)
        keyframe = self._find_keyframe_after(source, ts, self._keyframe_lookahead_seconds)
        if keyframe is None:
            return ts
        return keyframe

    def _find_keyframe_after(
        self,
        source: Path | str,
        timestamp: float,
        window: float,
    ) -> Optional[float]:
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            timestamp = 0.0
        if window <= 0:
            return None
        interval = f"{max(0.0, timestamp)}%+{window}"
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-skip_frame", "nokey",
            "-read_intervals", interval,
            "-show_entries", "frame=pkt_pts_time,best_effort_timestamp_time",
            "-of", "csv=p=0",
            str(source),
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                **_subprocess_kwargs(),
            )
        except subprocess.CalledProcessError:
            return None
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        for line in lines:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            for part in parts:
                try:
                    value = float(part)
                except (TypeError, ValueError):
                    continue
                if value >= timestamp:
                    return value
        return None

    def _pick_candidate_timestamps(
        self,
        duration: Optional[float],
        preferred: Optional[float],
    ) -> list[float]:
        candidates: list[float] = []

        def _add(ts: Optional[float]) -> None:
            if ts is None:
                return
            try:
                ts = float(ts)
            except (TypeError, ValueError):
                return
            if duration:
                ts = max(0.0, min(ts, max(0.0, duration - 0.05)))
            for existing in candidates:
                if abs(existing - ts) < 0.25:
                    return
            candidates.append(ts)

        _add(preferred)
        if duration:
            _add(duration * 0.10)
            _add(duration * 0.60)
            _add(duration * 0.80)
        else:
            _add(0.5)

        if len(candidates) > 3:
            candidates = candidates[:3]

        if not candidates:
            candidates.append(0.0)
        return candidates

    def _extract_video_frame(
        self,
        source: Path | str,
        size: Tuple[int, int] | QSize,
        timestamp: float,
    ) -> Optional[QImage]:
        w, h = self._normalize_size(size)

        # Try with hardware acceleration first
        if self._enable_hwaccel and self._hwaccel_detected:
            img = self._extract_video_frame_hwaccel(source, (w, h), timestamp)
            if img is not None:
                return img
            logger.debug(f"Hardware decode failed for {source}, falling back to software")

        # Fallback to software decoding
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-ss", str(max(timestamp, 0.0)),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", self._scale_filter((w, h)),
            "-f", "image2pipe",
            "-vcodec", "png",
            "-",
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                **_subprocess_kwargs(),
            )
        except subprocess.CalledProcessError:
            return None

        img = QImage.fromData(proc.stdout, "PNG")
        if img.isNull():
            return None
        return img

    def _extract_video_frame_hwaccel(
        self,
        source: Path | str,
        size: Tuple[int, int],
        timestamp: float,
    ) -> Optional[QImage]:
        """Extract video frame using hardware acceleration."""
        if not self._hwaccel_method:
            return None

        w, h = size
        cmd = [
            "ffmpeg",
            "-hwaccel", self._hwaccel_method,
            "-loglevel", "error",
            "-ss", str(max(timestamp, 0.0)),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", self._scale_filter((w, h)),
            "-f", "image2pipe",
            "-vcodec", "png",
            "-",
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                **_subprocess_kwargs(),
            )
        except subprocess.CalledProcessError:
            # Hardware decode failed, will fallback to software
            return None

        img = QImage.fromData(proc.stdout, "PNG")
        if img.isNull():
            return None
        return img

    def _score_frame(self, img: QImage) -> float:
        if img.isNull():
            return -1.0
        frame = img.convertToFormat(QImage.Format.Format_RGB888)
        width = frame.width()
        height = frame.height()
        if width <= 0 or height <= 0:
            return -1.0
        ptr = frame.bits()
        ptr.setsize(frame.bytesPerLine() * height)
        data = memoryview(ptr)
        stride = frame.bytesPerLine()

        hist = [0] * 256
        total = 0
        sum_luma = 0.0
        sum_sq = 0.0
        black = 0
        white = 0

        for y in range(height):
            row = data[y * stride:(y + 1) * stride]
            for x in range(0, width * 3, 3):
                r = row[x]
                g = row[x + 1]
                b = row[x + 2]
                luma = (r * 54 + g * 183 + b * 19) >> 8
                hist[luma] += 1
                total += 1
                sum_luma += luma
                sum_sq += luma * luma
                if luma < 16:
                    black += 1
                elif luma > 240:
                    white += 1

        if total == 0:
            return -1.0
        mean = sum_luma / total
        var = (sum_sq / total) - (mean * mean)
        entropy = 0.0
        for count in hist:
            if count:
                p = count / total
                entropy -= p * math.log2(p)

        black_ratio = black / total
        white_ratio = white / total
        if black_ratio > 0.85:
            return -2.0

        score = entropy
        score += min(var / (255.0 * 255.0), 1.0) * 2.0
        score -= black_ratio * 4.0
        score -= white_ratio * 2.0
        if mean < 20:
            score -= (20 - mean) / 20.0 * 2.0
        if mean > 235:
            score -= (mean - 235) / 20.0 * 2.0
        return score

    def _scale_filter(self, size: Tuple[int, int] | QSize) -> str:
        w, h = self._normalize_size(size)
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease"
