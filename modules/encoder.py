"""
Encoder detection and FFmpeg command builder.

Supports multiple hardware encoders with automatic detection and fallback:
- Intel Quick Sync Video (hevc_qsv)
- AMD VAAPI (hevc_vaapi)
- NVIDIA NVENC (hevc_nvenc)
- Software fallback (libx265)

Detection priority: QSV > VAAPI > NVENC > Software
Can be overridden via config.yaml encoder setting.
"""

import subprocess
import logging
import os

logger = logging.getLogger(__name__)

ENCODER_QSV = "qsv"
ENCODER_VAAPI = "vaapi"
ENCODER_NVENC = "nvenc"
ENCODER_SOFTWARE = "software"
ENCODER_AUTO = "auto"

FFMPEG_PATH = "/usr/lib/jellyfin-ffmpeg/ffmpeg"


def _check_qsv():
    """Check if Intel QSV is available by probing vainfo for iHD driver."""
    try:
        result = subprocess.run(
            ["vainfo"], capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        # QSV requires the iHD or i965 driver and HEVC encode entrypoint
        if "iHD" in output or "i965" in output:
            if "VAEntrypointEncSlice" in output:
                logger.info("Intel QSV detected (VA-API iHD/i965 driver with encode support)")
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def _check_vaapi():
    """Check if AMD VAAPI encoding is available."""
    try:
        result = subprocess.run(
            ["vainfo"], capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        # AMD uses radeonsi/mesa driver
        if "radeonsi" in output or "MESA" in output or "AMD" in output.upper():
            if "VAEntrypointEncSlice" in output:
                logger.info("AMD VAAPI detected (Mesa/radeonsi driver with encode support)")
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Also check if /dev/dri exists and vainfo reports any HEVC encode
    # (some AMD setups don't identify as radeonsi in vainfo output)
    if os.path.exists("/dev/dri/renderD128"):
        try:
            result = subprocess.run(
                ["vainfo"], capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            if "VAProfileHEVC" in output and "VAEntrypointEncSlice" in output:
                # Not Intel (already checked), so likely AMD
                if "iHD" not in output and "i965" not in output:
                    logger.info("VAAPI HEVC encode detected (non-Intel, likely AMD)")
                    return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return False


def _check_nvenc():
    """Check if NVIDIA NVENC is available."""
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("NVIDIA GPU detected via nvidia-smi")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def detect_encoder():
    """
    Auto-detect the best available hardware encoder.

    Returns one of: ENCODER_QSV, ENCODER_VAAPI, ENCODER_NVENC, ENCODER_SOFTWARE
    """
    logger.info("Auto-detecting hardware encoder...")

    if _check_qsv():
        return ENCODER_QSV
    if _check_vaapi():
        return ENCODER_VAAPI
    if _check_nvenc():
        return ENCODER_NVENC

    logger.info("No hardware encoder detected, falling back to software (libx265)")
    return ENCODER_SOFTWARE


def resolve_encoder(config_value):
    """
    Resolve the encoder to use based on config setting.

    Args:
        config_value: Value from config.yaml encoder field.
                      One of: 'auto', 'qsv', 'vaapi', 'nvenc', 'software', or None.

    Returns:
        Resolved encoder constant string.
    """
    if config_value is None or config_value == ENCODER_AUTO:
        return detect_encoder()

    valid = {ENCODER_QSV, ENCODER_VAAPI, ENCODER_NVENC, ENCODER_SOFTWARE}
    if config_value in valid:
        logger.info(f"Using configured encoder: {config_value}")
        return config_value

    logger.warning(f"Unknown encoder '{config_value}' in config, falling back to auto-detect")
    return detect_encoder()


def build_ffmpeg_cmd(input_path, output_path, encoder, quality, title):
    """
    Build the complete FFmpeg command for transcoding to HEVC.

    Args:
        input_path: Path to the input file (already renamed to _old).
        output_path: Path for the output file.
        encoder: One of the ENCODER_* constants.
        quality: Quality value (integer, typically 18-25).
        title: Metadata title to embed.

    Returns:
        List of command arguments suitable for subprocess/FfmpegProgress.
    """
    # Common input/mapping options
    cmd = [
        FFMPEG_PATH,
        "-i", input_path,
    ]

    # Encoder-specific options
    if encoder == ENCODER_QSV:
        cmd.extend([
            "-pix_fmt", "p010le",
            "-map_chapters", "0",
            "-metadata", f"title={title}",
            "-map", "0:v:0",
            "-c:v", "hevc_qsv",
            "-profile:v", "main10",
            "-preset", "medium",
            "-rc_mode", "CQP",
            "-global_quality", f"{quality}",
            "-look_ahead", "1",
            "-look_ahead_depth", "40",
            "-adaptive_i", "1",
            "-adaptive_b", "1",
        ])

    elif encoder == ENCODER_VAAPI:
        cmd = [
            FFMPEG_PATH,
            "-vaapi_device", "/dev/dri/renderD128",
            "-i", input_path,
            "-vf", "format=nv12|p010,hwupload",
            "-map_chapters", "0",
            "-metadata", f"title={title}",
            "-map", "0:v:0",
            "-c:v", "hevc_vaapi",
            "-profile:v", "main10",
            "-rc_mode", "CQP",
            "-global_quality", f"{quality}",
        ]

    elif encoder == ENCODER_NVENC:
        cmd.extend([
            "-pix_fmt", "p010le",
            "-map_chapters", "0",
            "-metadata", f"title={title}",
            "-map", "0:v:0",
            "-c:v", "hevc_nvenc",
            "-profile:v", "main10",
            "-preset", "p5",
            "-rc", "constqp",
            "-qp", f"{quality}",
            "-b_ref_mode", "middle",
        ])

    elif encoder == ENCODER_SOFTWARE:
        cmd.extend([
            "-pix_fmt", "yuv420p10le",
            "-map_chapters", "0",
            "-metadata", f"title={title}",
            "-map", "0:v:0",
            "-c:v", "libx265",
            "-preset", "medium",
            "-crf", f"{quality}",
            "-x265-params", "profile=main10:level-idc=5.1",
        ])

    else:
        raise ValueError(f"Unknown encoder: {encoder}")

    # Common output options (audio, subtitles, stats)
    cmd.extend([
        "-map", "0:a",
        "-c:a", "copy",
        "-map", "0:s?",
        "-c:s", "copy",
        "-stats_period", "15",
        output_path
    ])

    return cmd


def get_encoder_display_name(encoder):
    """Return a human-readable name for the encoder."""
    names = {
        ENCODER_QSV: "Intel QSV (hevc_qsv)",
        ENCODER_VAAPI: "AMD VAAPI (hevc_vaapi)",
        ENCODER_NVENC: "NVIDIA NVENC (hevc_nvenc)",
        ENCODER_SOFTWARE: "Software (libx265)",
    }
    return names.get(encoder, encoder)
