"""FFmpeg e arquivos baixados."""
import shutil
import subprocess
import sys
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ffmpeg():
    """O FFmpeg do sistema ou o que vem com o imageio-ffmpeg."""
    achado = shutil.which("ffmpeg")
    if achado:
        return achado
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def rodar_ffmpeg(args, timeout=None):
    exe = ffmpeg()
    if not exe:
        raise RuntimeError("FFmpeg nao encontrado")
    p = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError(f"FFmpeg falhou: {p.stderr.strip()[-400:]}")


def achar(pasta, nome):
    """Arquivo final <nome>.<ext>, ignorando os parciais do yt-dlp (.part, .ytdl, video.f137.mp4...)."""
    for p in Path(pasta).glob(f"{nome}.*"):
        if p.stem != nome or p.suffix in (".part", ".ytdl", ".tmp"):
            continue
        return p
    return None


def pasta_do_python():
    return Path(sys.executable).parent
