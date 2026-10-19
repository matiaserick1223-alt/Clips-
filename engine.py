"""Local video highlight detection and vertical clipping for content you own or can reuse."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np


class VideoError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    """Prefer a full system FFmpeg (often enables subtitles); otherwise bundled FFmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception as exc:
        raise VideoError("FFmpeg no está disponible. Instala FFmpeg.") from exc


def ffmpeg_has_ass(exe: str) -> bool:
    res = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True)
    return bool(re.search(r"\sass\s+V->V", res.stdout))


def run(cmd: list[str], cwd: Path | None = None) -> None:
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if res.returncode:
        msg = (res.stderr or res.stdout)[-1800:]
        raise VideoError(f"No se pudo procesar el video. Detalle de FFmpeg:\n{msg}")


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int


def get_video_info(path: Path, exe: str | None = None) -> VideoInfo:
    exe = exe or ffmpeg_bin()
    p = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    data = p.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", data)
    video_section = next((line for line in data.splitlines() if "Video:" in line and "Stream" in line), "")
    resolution = re.search(r"(?:^|[, ])(\d{2,5})x(\d{2,5})(?:[, \[]|$)", video_section)
    if not m or not resolution:
        raise VideoError("No se pudo leer el video. Prueba un MP4 o MOV válido.")
    length = int(m[1])*3600 + int(m[2])*60 + float(m[3])
    return VideoInfo(length, int(resolution[1]), int(resolution[2]))


def energy_profile(path: Path, exe: str, sample_rate: int = 4000, hop: float = 0.25) -> np.ndarray:
    """Audio loudness in non-overlapping buckets, without uploading audio to the cloud."""
    cmd = [exe, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode or not p.stdout:
        return np.array([], dtype=np.float32)
    raw = np.frombuffer(p.stdout, dtype=np.int16)
    size = max(1, int(sample_rate * hop))
    raw = raw[: len(raw) // size * size]
    if not len(raw):
        return np.array([], dtype=np.float32)
    chunks = raw.astype(np.float32).reshape(-1, size) / 32768.0
    rms = np.sqrt(np.mean(chunks * chunks, axis=1))
    return rms.astype(np.float32)


def select_clips(profile: np.ndarray, duration: float, clip_length: int, count: int,
                 hop: float = 0.25) -> list[dict]:
    """Rank exciting moments by loudness change, activity and density; avoid overlap."""
    if duration <= 0:
        return []
    length = min(float(clip_length), duration)
    if duration <= length + 1:
        return [{"start": 0.0, "end": duration, "score": 0.0}]
    step = max(2.0, min(5.0, length/5))
    candidate_starts = np.arange(0.0, max(0.0, duration - length) + .01, step).tolist()
    candidate_starts.append(max(0.0, duration - length))
    starts = sorted(set(round(s, 2) for s in candidate_starts))
    if not len(profile) or np.max(profile) < 0.00001:
        # Silent movies get distributed, non-overlapping segments.
        starts = np.linspace(0, duration - length, min(count, int(duration // length)), endpoint=True).tolist()
        return [{"start": round(s, 2), "end": round(s+length, 2), "score": 0.0} for s in starts]
    floor = float(np.percentile(profile, 25))
    ceiling = float(np.percentile(profile, 95))
    normalized = np.clip((profile - floor)/(ceiling - floor + 1e-8), 0, 2)
    scores = []
    for start in starts:
        a = int(start/hop)
        b = min(len(normalized), int((start+length)/hop))
        sample = normalized[a:b]
        if not len(sample):
            continue
        # Abrupt changes catch surprise/yells; long activity catches talking.
        loud = float(np.percentile(sample, 85))
        movement = float(np.mean(np.abs(np.diff(sample)))) if len(sample) > 1 else 0
        talk = float(np.mean(sample > .24))
        variation = float(np.std(sample))
        score = .35*loud + .35*movement + .20*talk + .10*variation
        scores.append((score, start))
    scores.sort(reverse=True)
    chosen = []
    for score, start in scores:
        end = start + length
        overlap = any(max(0, min(end, other["end"]) - max(start, other["start"])) > length*.16 for other in chosen)
        if not overlap:
            chosen.append({"start": round(start, 2), "end": round(end, 2), "score": round(score, 3)})
        if len(chosen) >= count:
            break
    return sorted(chosen, key=lambda c: c["start"])


def make_srt_time(sec: float) -> str:
    ms = int(round(max(0, sec)*1000))
    hh, rest = divmod(ms, 3600000)
    mm, rest = divmod(rest, 60000)
    ss, ms = divmod(rest, 1000)
    return f"{hh:02}:{mm:02}:{ss:02},{ms:03}"


def make_ass_time(sec: float) -> str:
    cs = int(round(max(0, sec)*100))
    hh, rest = divmod(cs, 360000)
    mm, rest = divmod(rest, 6000)
    ss, cs = divmod(rest, 100)
    return f"{hh}:{mm:02}:{ss:02}.{cs:02}"


def transcribe_clip(source: Path, start: float, length: float, work_dir: Path,
                    model, language: str, exe: str) -> tuple[list[tuple[float, float, str]], str]:
    wav = work_dir / "fragment.wav"
    run([exe, "-y", "-loglevel", "error", "-ss", str(start), "-i", str(source),
         "-t", str(length), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    segments, _ = model.transcribe(str(wav), language=None if language == "Auto" else language,
                                    beam_size=1, vad_filter=True, word_timestamps=True,
                                    condition_on_previous_text=False)
    words = []
    full_text = []
    for segment in segments:
        full_text.append(segment.text.strip())
        if segment.words:
            for word in segment.words:
                item = word.word.strip()
                if item:
                    words.append((float(word.start), float(word.end), item))
        else:
            words.append((float(segment.start), float(segment.end), segment.text.strip()))
    wav.unlink(missing_ok=True)
    cues = []
    group = []
    for i, word in enumerate(words):
        if group and (len(group) >= 5 or word[0] - group[0][0] >= 2.0 or word[0] - group[-1][1] > 0.6):
            cues.append((max(0, group[0][0]), min(length, group[-1][1] + .06),
                         " ".join(w[2] for w in group)))
            group = []
        group.append(word)
    if group:
        cues.append((max(0, group[0][0]), min(length, group[-1][1] + .1),
                     " ".join(w[2] for w in group)))
    return cues, " ".join(full_text).strip()


def write_caption_files(folder: Path, stem: str, cues: list[tuple[float, float, str]]) -> tuple[Path, Path]:
    srt = folder / f"{stem}.srt"
    ass = folder / f"{stem}.ass"
    srt_parts = []
    dialogues = []
    for i, (start, end, caption) in enumerate(cues, 1):
        if end <= start:
            continue
        clean = re.sub(r"[\r\n]+", " ", caption).strip()
        srt_parts.append(f"{i}\n{make_srt_time(start)} --> {make_srt_time(end)}\n{clean}\n")
        clean_ass = clean.replace("\\", "").replace("{", "(").replace("}", ")")
        # Max two short lines, highlighted white with black outline.
        if len(clean_ass) > 33:
            tokens = clean_ass.split()
            ix = len(tokens)//2
            clean_ass = " ".join(tokens[:ix]) + r"\N" + " ".join(tokens[ix:])
        dialogues.append(f"Dialogue: 0,{make_ass_time(start)},{make_ass_time(end)},Default,,0,0,0,,{clean_ass}")
    srt.write_text("\n".join(srt_parts), encoding="utf-8")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,1,2,36,36,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass.write_text(header + "\n".join(dialogues) + "\n", encoding="utf-8-sig")
    return srt, ass


def vertical_filter(mode: str) -> str:
    if mode == "Completo con fondo difuminado":
        return ("split=2[fg][bg];[bg]scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280,boxblur=18:2[bg2];"
                "[fg]scale=720:1280:force_original_aspect_ratio=decrease[fg2];"
                "[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30")
    return ("scale=720:1280:force_original_aspect_ratio=increase,"
            "crop=720:1280,setsar=1,fps=30")


def render_clip(source: Path, target: Path, start: float, length: float, exe: str,
                mode: str, ass_file: Path | None = None) -> None:
    vf = vertical_filter(mode)
    if ass_file and ass_file.exists():
        # Workdir contains caption so paths with Windows drive letters/spaces work.
        vf += f",ass={ass_file.name}"
    run([exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
         "-i", str(source.resolve()), "-t", f"{length:.3f}",
         "-vf", vf, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart",
         str(target.resolve())], cwd=target.parent)


def make_clips(source: Path, out_dir: Path, clip_length: int, count: int,
               subtitles: bool = True, language: str = "en", model_size: str = "tiny.en",
               mode: str = "Recorte vertical", callback: Callable[[str], None] | None = None) -> dict:
    def say(message):
        if callback:
            callback(message)
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = ffmpeg_bin()
    say("Analizando duración y formato…")
    info = get_video_info(source, exe)
    if info.duration < 3:
        raise VideoError("El video debe durar al menos 3 segundos.")
    say("Buscando reacciones y cambios de volumen…")
    profile = energy_profile(source, exe)
    selections = select_clips(profile, info.duration, clip_length, count)
    if not selections:
        raise VideoError("No se encontraron segmentos para cortar.")
    use_ass = ffmpeg_has_ass(exe)
    model = None
    notice = []
    if subtitles:
        say("Cargando IA de transcripción (primera vez: descarga del modelo)…")
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
        except Exception as exc:
            notice.append("No se pudieron generar subtítulos; verifica que tienes internet la primera vez y que faster-whisper esté instalado. " + str(exc)[:170])
    results = []
    for index, span in enumerate(selections, 1):
        say(f"Procesando clip {index} de {len(selections)}…")
        stem = f"clip_{index:02d}"
        clip_len = span["end"] - span["start"]
        caption_path, ass_path, transcript = None, None, ""
        if model:
            try:
                cues, transcript = transcribe_clip(source, span["start"], clip_len, out_dir, model, language, exe)
                if cues:
                    caption_path, ass_path = write_caption_files(out_dir, stem, cues)
            except Exception as exc:
                notice.append(f"Clip {index}: subtítulos omitidos ({str(exc)[:130]}).")
        target = out_dir / f"{stem}.mp4"
        burn = ass_path if use_ass else None
        if ass_path and not use_ass:
            notice.append("Tu FFmpeg no incluye libass. Los subtítulos se guardaron como .srt, sin incrustar en el video.")
        try:
            render_clip(source, target, span["start"], clip_len, exe, mode, burn)
        except VideoError:
            if burn:
                notice.append(f"Clip {index}: no se pudieron incrustar subtítulos; se generará el video sin ellos.")
                render_clip(source, target, span["start"], clip_len, exe, mode, None)
            else:
                raise
        excerpt = transcript[:80].strip()
        first = excerpt.split(".")[0][:65].strip(" ,!.?") if excerpt else "Momento destacado"
        title = f"{first} 😱 #KaiCenat #TwitchClips #Streaming" if first else "Momento destacado 😱 #KaiCenat #TwitchClips"
        info_obj = {**span, "name": target.name, "mp4": str(target),
                    "srt": str(caption_path) if caption_path else None,
                    "ass": str(ass_path) if ass_path else None,
                    "transcript": transcript, "caption": title}
        (out_dir / f"{stem}.txt").write_text(title, encoding="utf-8")
        results.append(info_obj)
    metadata = {"source_name": source.name, "video": asdict(info), "clips": results, "warnings": list(dict.fromkeys(notice)),
                "note": "Las selecciones son aproximadas, basadas en energía de audio. Revisa cada clip antes de publicar."}
    (out_dir / "manifest.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    say("Clips terminados")
    return metadata


def zip_results(out_dir: Path) -> Path:
    import zipfile
    dest = out_dir / "clips_tiktok.zip"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=2) as zf:
        for p in sorted(out_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in (".mp4", ".srt", ".txt", ".json"):
                zf.write(p, arcname=p.name)
    return dest
