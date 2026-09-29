"""Complemento "YouTube" para o IOkê: busca, trecho, audio (e o video do clipe) e a conta
Premium opcional. Fala com o app pelo SDK (karaoke_complemento.py, API 1)."""
import logging
import re

import conta as conta_mod
from karaoke_complemento import Cancelada, Complemento, Erro
from youtube import (Cancelado, YouTube, adivinhar, contexto, e_anti_robo, limpar, rotulo_audio, thumb_url,
                     video_id_do_texto)

log = logging.getLogger("complemento")
comp = Complemento()
yt = YouTube(cookies=lambda: conta.cookies(), navegador=lambda: comp.opcoes.get("navegador_cookies") or "")
conta = conta_mod.Conta(comp.dados, yt, idioma=lambda: comp.idioma)

TEXTOS = {
    "baixando": ("Baixando do YouTube...", "Downloading from YouTube..."),
    "video": ("Baixando o vídeo do clipe...", "Downloading the music video..."),
    "sem_conta": ("Sem conta do YouTube (o áudio vem na qualidade comum).", "No YouTube account (audio in regular quality)."),
    "conta": ("Conta: {nome}{premium}{audio}", "Account: {nome}{premium}{audio}"),
    "premium": (" · Premium", " · Premium"),
    "conferindo": ("Verificando a conta...", "Checking the account..."),
    "esperando": ("Entre na sua conta na janela do {nav} que abriu.", "Sign in to your account in the {nav} window that opened."),
    "entrar_aberto": ("Entre na sua conta na janela que abriu. A senha é digitada no site do Google, nunca aqui.",
                      "Sign in in the window that opened. The password is typed on Google's site, never here."),
    "chrome": ("Não achei o Google Chrome nem o Microsoft Edge neste PC.", "Couldn't find Google Chrome or Microsoft Edge on this PC."),
    "fechada": ("A janela foi fechada antes de terminar o login.", "The window was closed before the sign-in finished."),
    "demorou": ("O login demorou demais. Tente de novo.", "The sign-in took too long. Try again."),
    "nao_reconheceu": ("O YouTube não reconheceu o login. Entre de novo.", "YouTube didn't recognize the sign-in. Sign in again."),
    "conferir_ok": ("Verificando a conta: o resultado aparece aqui em alguns segundos.", "Checking the account: the result shows up here in a few seconds."),
    "sem_cookies": ("Nenhuma conta conectada.", "No account connected."),
    "saiu": ("Conta desconectada.", "Account disconnected."),
    # erros do YouTube, com o que fazer
    "idade": ("Vídeo com restrição de idade: conecte a conta do YouTube nas opções do complemento e tente de novo.",
              "Age-restricted video: connect the YouTube account in the add-on and try again."),
    "robo": ("O YouTube bloqueou os downloads por um tempo (anti-robô). Espere alguns minutos e tente de novo (ou conecte a conta do YouTube).",
             "YouTube blocked downloads for a while (anti-bot). Wait a few minutes and try again (or connect the YouTube account)."),
    "privado": ("Este vídeo é privado: escolha outro vídeo da mesma música.", "This video is private: choose another video of the same song."),
    "membros": ("Vídeo só para membros do canal: escolha outro vídeo da mesma música.", "Members-only video: choose another video of the same song."),
    "direitos": ("Vídeo removido por direitos autorais: escolha outro vídeo da mesma música.", "Video removed for copyright: choose another video of the same song."),
    "estreia": ("Este vídeo ainda não estreou: escolha outro vídeo da mesma música.", "This video hasn't premiered yet: choose another video of the same song."),
    "indisponivel": ("Vídeo indisponível no YouTube (removido ou bloqueado aqui): escolha outro vídeo da mesma música.",
                     "Video unavailable on YouTube (removed or blocked here): choose another video of the same song."),
    "internet": ("Sem conexão com o YouTube. Confira a internet e tente de novo.", "No connection to YouTube. Check the internet and try again."),
}


def t(chave, **params):
    pt, en = TEXTOS[chave]
    texto = pt if comp.idioma == "pt-BR" else en
    for k, v in params.items():
        texto = texto.replace("{" + k + "}", str(v))
    return texto


def amigavel(exc):
    """O erro do yt-dlp numa frase com o que fazer (senao, a mensagem original limpa)."""
    msg = limpar(exc)
    low = msg.lower()
    if "confirm your age" in low or "age-restricted" in low or "inappropriate for some users" in low:
        return t("idade")
    if e_anti_robo(msg):
        return t("robo")
    if "private video" in low:
        return t("privado")
    if "members-only" in low or "join this channel" in low:
        return t("membros")
    if "copyright" in low:
        return t("direitos")
    if "premieres in" in low or "live event will begin" in low:
        return t("estreia")
    if any(x in low for x in ("video unavailable", "video is unavailable", "video is not available",
                              "available in your country", "has been removed", "no longer available")):
        return t("indisponivel")
    if any(x in low for x in ("getaddrinfo failed", "failed to resolve", "name resolution", "network is unreachable",
                              "unable to download webpage", "unable to download api page", "connection refused",
                              "timed out", "unable to connect to proxy")):
        return t("internet")
    return msg or exc.__class__.__name__


@comp.estado
def estado():
    if conta.fase == "esperando":
        return {"texto": t("esperando", nav=conta.mensagem), "nivel": "aviso"}
    if conta.fase == "conferindo":
        return {"texto": t("conferindo"), "nivel": "aviso"}
    if conta.fase == "erro" and conta.mensagem:
        chave = conta.mensagem if conta.mensagem in TEXTOS else None
        return {"texto": t(chave) if chave else conta.mensagem, "nivel": "erro"}
    s = conta.status or {}
    if not conta.cookies() or not s.get("logged_in"):
        return {"texto": t("sem_conta"), "nivel": "ok"}
    audio = s.get("best_audio") or {}
    return {"texto": t("conta", nome=s.get("name") or s.get("email") or "YouTube",
                       premium=t("premium") if s.get("premium") else "",
                       audio=f" · {audio.get('codec')} {audio.get('kbps')} kbps" if audio.get("kbps") else ""),
            "nivel": "ok"}


@comp.acao("entrar")
def entrar(_dados):
    try:
        conta.entrar()
    except RuntimeError as exc:
        raise Erro(t("chrome") if str(exc) == "chrome" else str(exc)) from None
    return {"texto": t("entrar_aberto")}


@comp.acao("conferir")
def conferir(_dados):
    return {"texto": t("conferir_ok") if conta.conferir() else t("sem_cookies")}


@comp.acao("sair")
def sair(_dados):
    conta.sair()
    return {"texto": t("saiu")}


@comp.buscar
def buscar(texto, limite):
    try:
        n = int(comp.opcoes.get("resultados") or 5)
    except ValueError:
        n = 5
    try:
        return yt.buscar(texto, min(limite, n) if limite else n)
    except Exception as exc:  # noqa: BLE001
        raise Erro(amigavel(exc)) from None


@comp.trecho
def trecho(ref, destino):
    try:
        return yt.trecho(str(ref), destino)
    except Exception as exc:  # noqa: BLE001
        raise Erro(amigavel(exc)) from None


def altura_video():
    """A qualidade do video escolhida nas opcoes do complemento (linhas: 360, 480, 720 ou 1080)."""
    try:
        return int(comp.opcoes.get("qualidade_video") or 720)
    except ValueError:
        return 720


ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extrair_id(*valores):
    """O id do video no primeiro valor que tiver um: o id puro, um link do YouTube ou "youtube:<id>"."""
    for v in valores:
        v = str(v or "").strip()
        if v.startswith("youtube:"):
            v = v.split(":", 1)[1]
        if ID_RE.match(v):
            return v
        vid = video_id_do_texto(v)
        if vid:
            return vid
    return None


def id_do_video(ref, chave=None):
    vid = extrair_id(ref, chave)
    if not vid:
        raise Erro("id de vídeo inválido" if comp.idioma == "pt-BR" else "invalid video id")
    return vid


@comp.reconhecer
def reconhecer(musica):
    """Musicas de versoes antigas do IOkê, de antes dos complementos: o id do video ficava em "youtube_id" (ou
    no link) e o contexto em "yt". Viram musicas deste complemento no formato de hoje; as novas ja nascem nele."""
    vid = extrair_id(musica.get("youtube_id"), musica.get("webpage_url"), musica.get("url"))
    if not vid:
        return None
    antigo = musica.get("yt") if isinstance(musica.get("yt"), dict) else {}
    info = {"tem_video": True, "capa_url": thumb_url(vid, "hqdefault")}
    if musica.get("title"):
        info["titulo"] = musica["title"]
    if musica.get("artist"):
        info["artista"] = musica["artist"]
    if musica.get("duration"):
        info["duracao"] = musica["duration"]
    r = {"ref": vid, "chave": vid, "info": info}  # a chave e o id: o id da musica no IOkê nao muda
    if antigo:
        r["contexto"] = {k: str(antigo.get(k) or "") for k in ("title", "channel", "description")}
    return r


@comp.acao_musica("baixar_de_novo")
def baixar_de_novo(tarefa, musica, destino):
    """O audio de novo (o melhor liberado agora, com a conta se tiver): o IOkê troca o audio e separa de novo."""
    vid = id_do_video(musica.get("ref"), musica.get("chave"))
    try:
        tarefa.progresso(0.0, t("baixando"))
        info, audio, _bruto, com_conta = yt.baixar_audio(vid, destino, tarefa.progresso, lambda: tarefa.cancelada)
    except (Cancelado, Cancelada):
        raise Cancelada() from None
    except Exception as exc:  # noqa: BLE001
        raise Erro(amigavel(exc)) from None
    rot = rotulo_audio(info)
    return {"audio": audio.name, "qualidade": {"codec": rot["codec"], "kbps": rot["kbps"], "conta": com_conta}}


@comp.acao_musica("baixar_video")
def baixar_video(tarefa, musica, destino):
    """So o video do clipe (sem audio): o IOkê usa como fundo."""
    vid = id_do_video(musica.get("ref"), musica.get("chave"))
    try:
        tarefa.progresso(0.0, t("video"))
        arquivo = yt.baixar_video(vid, destino, progresso=tarefa.progresso, cancelado=lambda: tarefa.cancelada,
                                  altura=altura_video())
    except (Cancelado, Cancelada):
        raise Cancelada() from None
    except Exception as exc:  # noqa: BLE001
        raise Erro(amigavel(exc)) from None
    return {"video": arquivo.name}


@comp.obter
def obter(tarefa, ref, destino, video):
    vid = id_do_video(ref)
    cancelado = lambda: tarefa.cancelada  # noqa: E731
    try:
        tarefa.progresso(0.0, t("baixando"))
        info, audio, bruto, com_conta = yt.baixar_audio(vid, destino, lambda f: tarefa.progresso(f * (0.6 if video else 1.0)),
                                                        cancelado)
        arquivo_video = None
        if video:
            tarefa.progresso(0.6, t("video"))
            try:
                arquivo_video = yt.baixar_video(vid, destino, bruto, com_conta, lambda f: tarefa.progresso(0.6 + 0.4 * f),
                                                cancelado, altura=altura_video())
            except Cancelado:
                raise
            except Exception as exc:  # noqa: BLE001 - sem o video, a musica continua
                log.warning("video de %s falhou: %s", vid, limpar(exc))
    except (Cancelado, Cancelada):
        raise Cancelada() from None
    except Erro:
        raise
    except Exception as exc:  # noqa: BLE001
        raise Erro(amigavel(exc)) from None
    palpite = adivinhar(info)
    rot = rotulo_audio(info)
    data = str(info.get("release_date") or info.get("upload_date") or "")
    return {
        "audio": audio.name,
        "video": arquivo_video.name if arquivo_video else None,
        "info": {"titulo": palpite["titulo"], "artista": palpite["artista"], "album": palpite.get("album"),
                 "ano": data[:4] or None, "duracao": info.get("duration"), "contexto": contexto(info),
                 "capa_url": thumb_url(vid, "hqdefault")},
        "qualidade": {"codec": rot["codec"], "kbps": rot["kbps"], "conta": com_conta},
    }


if __name__ == "__main__":
    comp.rodar()
