"""Busca, trecho e download de audio e video do YouTube (com o yt-dlp)."""
import copy
import logging
from concurrent.futures import ThreadPoolExecutor
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus

import yt_dlp
from yt_dlp.utils import DownloadCancelled

import midia
from nomes import clean_channel, clean_track

log = logging.getLogger("complemento.youtube")

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
# listas: uma playlist (?list=...) ou um album do YouTube Music (/browse/MPREb_...)
LISTA_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]{10,})")
ALBUM_RE = re.compile(r"/browse/(MPREb_[A-Za-z0-9_-]+)")
LISTA_REF_RE = re.compile(r"^(?:MPREb_)?[A-Za-z0-9_-]{10,}$")
LISTA_MAX = 300  # musicas de uma lista (um "Mix" do YouTube nao tem fim)
ALBUNS_NA_BUSCA = 2  # albuns do YouTube Music junto com os videos, na busca por texto
_INDISPONIVEIS = ("[private video]", "[deleted video]", "[video privado]", "[vídeo privado]")
# Melhor audio que o YouTube oferecer: normalmente o Opus ~130-160 kbps (itag 251); com uma conta
# Premium, o Opus ~256 kbps (itag 774) ou o AAC 256 kbps (itag 141), pelo cliente do YouTube Music.
AUDIO_FORMAT = "bestaudio/best"


class Cancelado(Exception):
    pass


class YouTube:
    """Tudo o que fala com o YouTube. `cookies()` devolve o arquivo de cookies da conta (ou None);
    `navegador()` o navegador para ler cookies (opcao avancada), ou ""."""

    def __init__(self, cookies=lambda: None, navegador=lambda: ""):
        self.cookies = cookies
        self.navegador = navegador
        self._locks = {}
        self._guarda = threading.Lock()

    def _opts(self, com_conta=False, **extra):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            # o YouTube exige um runtime JavaScript para as assinaturas (o deno vem nas dependencias)
            "js_runtimes": {"deno": {}, "node": {}},
        }
        ff = midia.ffmpeg()
        if ff and not shutil.which("ffmpeg"):
            opts["ffmpeg_location"] = ff
        if com_conta:
            arquivo = self.cookies()
            if arquivo:
                opts["cookiefile"] = str(arquivo)
            elif self.navegador():
                opts["cookiesfrombrowser"] = (self.navegador(),)
            # os formatos Premium so aparecem para o cliente do YouTube Music
            opts["extractor_args"] = {"youtube": {"player_client": ["default", "web_music"]}}
        opts.update(extra)
        return opts

    def tem_conta(self):
        return bool(self.cookies() or self.navegador())

    # ------------------------------------------------------------ busca
    def buscar(self, texto, limite=5):
        """Os videos da busca e, por texto, ate ALBUNS_NA_BUSCA albuns do YouTube Music. Um link colado: o video
        e/ou a lista dele (uma playlist, um album)."""
        lista, vid = lista_do_texto(texto), video_id_do_texto(texto)
        if lista or vid:
            out = [self.resultado_da_lista(lista)] if lista else []
            if vid:
                with yt_dlp.YoutubeDL(self._opts(skip_download=True)) as ydl:
                    out.append(resultado(ydl.extract_info(watch_url(vid), download=False, process=False)))
            return out
        with ThreadPoolExecutor(max_workers=2) as pool:
            albuns = pool.submit(self._albuns, texto)
            with yt_dlp.YoutubeDL(self._opts(skip_download=True, extract_flat="in_playlist")) as ydl:
                r = ydl.extract_info(f"ytsearch{int(limite)}:{texto}", download=False)
            videos = [resultado(e) for e in r.get("entries") or [] if e and e.get("id") and VIDEO_ID_RE.match(e["id"])]
            try:
                return albuns.result(timeout=15) + videos
            except Exception as exc:  # noqa: BLE001 - sem os albuns, os videos continuam
                log.info("albuns na busca: %s", limpar(exc))
                return videos

    def _albuns(self, texto):
        """Os primeiros albuns do YouTube Music para o texto (nomes limpos e faixas na ordem)."""
        busca = f"https://music.youtube.com/search?q={quote_plus(texto)}#albums"
        with yt_dlp.YoutubeDL(self._opts(skip_download=True, extract_flat="in_playlist",
                                         playlistend=ALBUNS_NA_BUSCA)) as ydl:
            r = ydl.extract_info(busca, download=False)
        refs = [e["id"] for e in r.get("entries") or [] if e and str(e.get("id") or "").startswith("MPREb_")]
        with ThreadPoolExecutor(max_workers=max(1, len(refs))) as pool:
            return [x for x in pool.map(self._resultado_ou_nada, refs[:ALBUNS_NA_BUSCA]) if x]

    def _resultado_ou_nada(self, ref):
        try:
            return self.resultado_da_lista(ref)
        except Exception as exc:  # noqa: BLE001
            log.info("album %s: %s", ref, limpar(exc))
            return None

    # ------------------------------------------------------------ listas (playlists e albuns)
    def info_da_lista(self, ref, ate=LISTA_MAX):
        if not LISTA_REF_RE.match(ref or ""):
            raise ValueError("lista invalida")
        with yt_dlp.YoutubeDL(self._opts(skip_download=True, extract_flat="in_playlist", noplaylist=False,
                                         playlistend=ate)) as ydl:
            return ydl.extract_info(url_da_lista(ref), download=False)

    def resultado_da_lista(self, ref):
        """O cartao da lista na busca: o nome, o canal (ou o artista do album), a capa e quantas musicas."""
        info = self.info_da_lista(ref, ate=1)
        album, titulo = nome_do_album(info)
        primeira = next(iter(info.get("entries") or []), None) or {}
        artista = (primeira.get("channel") or primeira.get("uploader") or "") if album else \
            (info.get("channel") or info.get("uploader") or "")
        return {"ref": ref, "chave": None, "titulo": titulo, "artista": artista, "tipo": "lista",
                "quantidade": info.get("playlist_count"), "capa_url": capa_da_lista(info),
                "tem_trecho": False, "tem_video": False}

    def lista(self, ref):
        """As musicas da lista, no formato dos resultados da busca. Album: com o nome dele e o numero da faixa."""
        info = self.info_da_lista(ref)
        album, titulo = nome_do_album(info)
        itens = []
        for e in info.get("entries") or []:
            if not e or not VIDEO_ID_RE.match(str(e.get("id") or "")):
                continue
            if str(e.get("title") or "").strip().lower() in _INDISPONIVEIS:
                continue  # privado ou apagado: nao da para baixar
            it = resultado(e)
            if album:  # o YouTube Music ja da o nome da faixa e o artista
                it.update(album=titulo, faixa=len(itens) + 1)
            else:  # playlist comum: "Artista - Musica (Clipe Oficial)" no titulo do video, como ao baixar
                palpite = adivinhar(e)
                it.update(titulo=palpite["titulo"] or it["titulo"], artista=palpite["artista"] or it["artista"])
            itens.append(it)
        artista = (itens[0]["artista"] if itens else "") if album else (info.get("channel") or info.get("uploader") or "")
        return {"titulo": titulo, "artista": artista, "capa_url": capa_da_lista(info), "itens": itens}

    # ------------------------------------------------------------ trecho
    def trecho(self, video_id, destino):
        """~7 s do meio da musica (para conferir se e a versao certa)."""
        if not VIDEO_ID_RE.match(video_id or ""):
            raise ValueError("id de video invalido")
        destino = Path(destino)
        destino.mkdir(parents=True, exist_ok=True)
        arquivo = destino / f"{video_id}.mp3"
        with self._guarda:
            lock = self._locks.setdefault(video_id, threading.Lock())
        with lock:
            if arquivo.exists() and arquivo.stat().st_size > 0:
                return arquivo
            with yt_dlp.YoutubeDL(self._opts(skip_download=True, format="bestaudio/best")) as ydl:
                info = ydl.extract_info(watch_url(video_id), download=False)
            url, headers = info.get("url"), info.get("http_headers") or {}
            if not url:
                raise RuntimeError("nao consegui obter o audio do video")
            inicio = max(0.0, float(info.get("duration") or 60) * 0.45)
            args = ["-ss", f"{inicio:.1f}"]
            blob = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            if blob:
                args += ["-headers", blob]
            tmp = arquivo.with_name(f"{video_id}.part.mp3")
            args += ["-i", url, "-t", "7", "-vn", "-ac", "2", "-ar", "44100", "-b:a", "128k",
                     "-af", "afade=t=in:d=0.3,afade=t=out:st=6.3:d=0.7", str(tmp)]
            midia.rodar_ffmpeg(args, timeout=60)
            os.replace(tmp, arquivo)
            return arquivo

    # ------------------------------------------------------------ download
    def extrair(self, video_id, com_conta=False):
        with yt_dlp.YoutubeDL(self._opts(com_conta)) as ydl:
            return ydl.extract_info(watch_url(video_id), download=False, process=False)

    def _baixar(self, bruto, fmt, modelo, progresso, cancelado, com_conta):
        def gancho(d):
            if cancelado():
                raise DownloadCancelled()
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    progresso(min(1.0, (d.get("downloaded_bytes") or 0) / total))

        opts = self._opts(com_conta, format=fmt, outtmpl=modelo, progress_hooks=[gancho], overwrites=True,
                          retries=5, fragment_retries=5)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.process_ie_result(copy.deepcopy(bruto), download=True)

    def _tentativas(self, rodar, cancelado, vezes):
        """O YouTube as vezes responde 403 para uma URL; extrair de novo quase sempre resolve."""
        for n in range(1, vezes + 1):
            try:
                return rodar(n)
            except DownloadCancelled as exc:
                raise Cancelado() from exc
            except yt_dlp.utils.DownloadError as exc:
                if cancelado():
                    raise Cancelado() from exc
                if n == vezes or not _passageiro(exc):
                    raise
                time.sleep(2 * n)
        return None

    def _modos(self):
        """Com conta: tenta com ela e, se falhar, sem login."""
        return [True, False] if self.tem_conta() else [False]

    def baixar_audio(self, video_id, destino, progresso, cancelado):
        """-> (info, arquivo, bruto, com_conta). O bruto serve para baixar o video sem consultar de novo."""
        destino = Path(destino)
        modos = self._modos()
        estado = {}
        for com_conta in modos:
            def rodar(n, com_conta=com_conta):
                estado["bruto"] = self.extrair(video_id, com_conta)
                return self._baixar(estado["bruto"], AUDIO_FORMAT, str(destino / "audio.%(ext)s"), progresso,
                                    cancelado, com_conta)
            try:
                info = self._tentativas(rodar, cancelado, 3)
                break
            except Cancelado:
                raise
            except Exception as exc:  # noqa: BLE001
                if com_conta and len(modos) > 1:
                    log.warning("download com a conta falhou (%s); baixando sem login", limpar(exc))
                    continue
                raise
        arquivo = midia.achar(destino, "audio")
        if not arquivo:
            raise RuntimeError("o download terminou mas o arquivo nao foi encontrado")
        return info, arquivo, estado.get("bruto"), com_conta

    def baixar_video(self, video_id, destino, bruto=None, bruto_conta=False, progresso=lambda f: None,
                     cancelado=lambda: False, altura=720):
        """So a imagem (sem audio), para o fundo do player."""
        destino = Path(destino)
        modos = self._modos()
        for com_conta in modos:
            def rodar(n, com_conta=com_conta):
                info = bruto if (bruto is not None and n == 1 and bruto_conta == com_conta) else self.extrair(
                    video_id, com_conta)
                return self._baixar(info, formato_video(altura), str(destino / "video.%(ext)s"), progresso,
                                    cancelado, com_conta)
            try:
                self._tentativas(rodar, cancelado, 2)
                break
            except Cancelado:
                raise
            except Exception as exc:  # noqa: BLE001
                if com_conta and len(modos) > 1:
                    log.warning("video com a conta falhou (%s); baixando sem login", limpar(exc))
                    continue
                raise
        arquivo = midia.achar(destino, "video")
        if not arquivo:
            raise RuntimeError("o download do video terminou mas o arquivo nao foi encontrado")
        return arquivo

    def melhor_audio(self, video_id, com_conta=True):
        """O melhor formato so-audio liberado para este video (com ou sem a conta)."""
        info = self.extrair(video_id, com_conta)
        with yt_dlp.YoutubeDL(self._opts(com_conta, format=AUDIO_FORMAT)) as ydl:
            escolhido = ydl.process_ie_result(dict(info), download=False)
        return rotulo_audio(escolhido)


# ---------------------------------------------------------------- ajudantes
def watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def lista_do_texto(texto):
    """A lista de um link colado (?list=... ou um album /browse/MPREb_...), ou None."""
    texto = (texto or "").strip()
    if "youtu" not in texto:
        return None
    m = ALBUM_RE.search(texto) or LISTA_RE.search(texto)
    return m.group(1) if m else None


def url_da_lista(ref):
    if ref.startswith("MPREb_"):
        return f"https://music.youtube.com/browse/{ref}"
    return f"https://www.youtube.com/playlist?list={ref}"


def nome_do_album(info):
    """(e um album do YouTube Music?, o nome para mostrar). Os albuns vem como "Album - Nome" (playlist OLAK5uy_)."""
    titulo = str(info.get("title") or info.get("id") or "")
    album = str(info.get("id") or "").startswith("OLAK5uy_") or titulo.startswith(("Album - ", "Álbum - "))
    if album:
        titulo = re.sub(r"^(?:Album|Álbum) - ", "", titulo)
    return album, titulo


def capa_da_lista(info):
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    if thumbs:
        return max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))["url"]
    primeira = next(iter(info.get("entries") or []), None) or {}
    return thumb_url(primeira["id"]) if VIDEO_ID_RE.match(str(primeira.get("id") or "")) else None


def thumb_url(video_id, qualidade="mqdefault"):
    return f"https://i.ytimg.com/vi/{video_id}/{qualidade}.jpg"


def video_id_do_texto(texto):
    texto = (texto or "").strip()
    if "youtu" in texto or texto.startswith("http"):
        m = URL_ID_RE.search(texto)
        if m:
            return m.group(1)
    return None


def resultado(info):
    """Um resultado no formato do IOkê (a chave e o id do video)."""
    vid = info["id"]
    return {"ref": vid, "chave": vid, "titulo": info.get("title") or vid,
            "artista": info.get("channel") or info.get("uploader") or "", "duracao": info.get("duration"),
            "capa_url": thumb_url(vid), "tem_trecho": True, "tem_video": True, "extra": {"url": watch_url(vid)}}


def rotulo_audio(fmt):
    codec = (fmt.get("acodec") or "").split(".")[0]
    nome = {"opus": "Opus", "mp4a": "AAC", "vorbis": "Vorbis"}.get(codec, codec.upper() or "?")
    kbps = round(fmt.get("abr") or fmt.get("tbr") or 0)
    return {"itag": fmt.get("format_id"), "codec": nome, "kbps": kbps, "premium": kbps >= 192}


def formato_video(altura=720):
    # avc1 (H.264) primeiro: decodifica por hardware em qualquer navegador
    h = int(altura)
    return (f"bestvideo[height<={h}][vcodec^=avc1]/bestvideo[height<={h}][ext=mp4]/"
            f"bestvideo[height<={h}]/best[height<={h}]/bestvideo/best")


def adivinhar(info):
    """Artista e musica: os metadados do YouTube Music, ou "Artista - Musica" no titulo."""
    track = info.get("track")
    artist = info.get("artist") or info.get("creator")
    if not artist and info.get("artists"):
        artist = info["artists"][0]
    album = info.get("album")
    if track and artist:
        artist = re.split(r",|&| e | x ", artist)[0].strip() or artist
        return {"artista": artist, "titulo": clean_track(track) or track, "album": album}
    title = info.get("title") or ""
    parts = re.split(r"\s+[-–—]\s+|\s+-\s*|\s*-\s+", title, maxsplit=1)
    if len(parts) == 2 and clean_track(parts[1]):
        return {"artista": clean_track(parts[0]), "titulo": clean_track(parts[1]), "album": album}
    return {"artista": clean_channel(info.get("channel") or info.get("uploader")),
            "titulo": clean_track(title) or title, "album": album}


def contexto(info):
    """O que ajuda o IOkê a reconhecer a versao (ao vivo, remaster...) na escolha da letra."""
    data = info.get("upload_date") or ""
    return {"title": info.get("title") or "", "channel": info.get("channel") or info.get("uploader") or "",
            "description": ((info.get("description") or "")[:1500] + (f" ({data[:4]})" if data else "")).strip()}


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PREFIXO = re.compile(r"^ERROR:\s*(\[[^\]]+\]\s*[\w-]+:\s*)?")


def limpar(exc):
    return _PREFIXO.sub("", _ANSI.sub("", str(exc or "")).strip()).strip()


def e_anti_robo(msg):
    return any(t in msg for t in ("Sign in to confirm", "not a bot", "429", "Too Many Requests"))


def _passageiro(exc):
    msg = str(exc)
    if e_anti_robo(msg):
        return False  # insistir so piora o bloqueio
    return any(t in msg for t in ("403", "timed out", "Connection", "Read timed", "IncompleteRead"))
