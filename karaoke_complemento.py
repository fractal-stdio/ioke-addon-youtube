"""SDK dos complementos do IOkê (API 1). Um arquivo so, so com a biblioteca padrao do Python:
copie para o seu complemento e importe. Guia: docs/COMPLEMENTOS.md.

    from karaoke_complemento import Complemento, Erro

    comp = Complemento()

    @comp.buscar
    def buscar(texto, limite):
        return [{"ref": "1", "chave": "meu-id:1", "titulo": "Musica", "artista": "Artista",
                 "tem_trecho": False, "tem_video": False}]

    @comp.obter
    def obter(tarefa, ref, destino, video):
        tarefa.progresso(0.5, "Copiando...")
        ...
        return {"audio": "musica.mp3", "info": {"titulo": "Musica", "artista": "Artista"}}

    @comp.acao_musica("pegar_de_novo")  # declarada em "acoes_musica" no manifesto
    def pegar_de_novo(tarefa, musica, destino):
        ...
        return {"audio": "musica.flac"}  # o app troca o audio da musica (e separa de novo)

    comp.rodar()

O app liga o complemento com as variaveis KARAOKE_COMPLEMENTO_PORTA, KARAOKE_COMPLEMENTO_SENHA,
KARAOKE_COMPLEMENTO_DADOS (a pasta de dados dele), KARAOKE_IDIOMA e KARAOKE_API. So atende
127.0.0.1 e so pedidos com o cabecalho X-Senha certo.
"""
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API = 1
log = logging.getLogger("complemento")


class Erro(Exception):
    """Um erro para a pessoa ler (ja no idioma dela: use comp.idioma). Vira {"erro": mensagem}."""

    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


class Cancelada(Exception):
    """A tarefa foi cancelada pelo app (levantada por tarefa.conferir())."""


class Tarefa:
    """Uma tarefa longa (obter uma musica, uma acao da musica). Informe o andamento com progresso()
    e confira o cancelamento com conferir() de tempos em tempos."""

    def __init__(self, tipo, dados):
        self.id = uuid.uuid4().hex
        self.tipo = tipo
        self.dados = dados
        self.estado = "rodando"
        self.fracao = 0.0
        self.etapa = ""
        self.resultado = None
        self.erro = None
        self.cancelada = False
        self.em = time.time()

    def progresso(self, fracao, etapa=None):
        self.conferir()
        self.fracao = max(0.0, min(1.0, float(fracao)))
        if etapa is not None:
            self.etapa = str(etapa)
        self.em = time.time()

    def conferir(self):
        if self.cancelada:
            raise Cancelada()

    def publico(self):
        out = {"estado": self.estado, "fracao": round(self.fracao, 4), "etapa": self.etapa}
        if self.resultado is not None:
            out["resultado"] = self.resultado
        if self.erro:
            out["erro"] = self.erro
        return out


class Complemento:
    def __init__(self, pasta=None):
        self.pasta = Path(pasta or Path(sys.argv[0]).resolve().parent)
        self.porta = int(os.environ.get("KARAOKE_COMPLEMENTO_PORTA") or 0)
        self.senha = os.environ.get("KARAOKE_COMPLEMENTO_SENHA") or ""
        self.dados = Path(os.environ.get("KARAOKE_COMPLEMENTO_DADOS") or self.pasta / "dados")
        self.dados.mkdir(parents=True, exist_ok=True)
        self.idioma = os.environ.get("KARAOKE_IDIOMA") or "en"
        self.opcoes = {}
        self.manifesto = {}
        try:
            self.manifesto = json.loads((self.pasta / "karaoke-complemento.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self._rotas = {}
        self._acoes = {}
        self._acoes_musica = {}
        self._tarefas = {}
        self._lock = threading.Lock()
        self._ao_configurar = None
        self._estado = None
        self._obter = None
        logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                            format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # ------------------------------------------------------------ decoradores
    def configurar(self, fn):
        """fn(opcoes, idioma): chamado ao ligar e quando as opcoes mudam (depois de guardar em self.opcoes)."""
        self._ao_configurar = fn
        return fn

    def estado(self, fn):
        """fn() -> {"texto", "nivel": "ok"|"aviso"|"erro"}: aparece nas Configuracoes."""
        self._estado = fn
        return fn

    def acao(self, acao_id):
        """fn(dados) -> {"texto", "abrir_url"?}: um botao declarado em "acoes" (nas Configuracoes)."""
        def deco(fn):
            self._acoes[acao_id] = fn
            return fn
        return deco

    def acao_musica(self, acao_id):
        """fn(tarefa, musica, destino) -> {"audio"?, "video"?, "qualidade"?, "texto"?}: um botao declarado em
        "acoes_musica", que aparece nas musicas que vieram deste complemento. Roda como tarefa. `musica`:
        {"id", "titulo", "artista", "album", "duracao", "ref", "chave", "info"} (ref/chave/info: os que o
        obter deu). Os arquivos ficam dentro de destino: "audio" troca o audio da musica (e o app separa
        de novo, mantendo letra, capa, tom e ajustes); "video" vira o fundo. "texto": aparece na musica."""
        def deco(fn):
            self._acoes_musica[acao_id] = fn
            return fn
        return deco

    def buscar(self, fn):
        """fn(texto, limite) -> [{"ref", "chave", "titulo", "artista", ...}] (fonte_musicas)."""
        self._rotas["/fonte/buscar"] = lambda d: {"resultados": fn(str(d.get("texto") or ""), int(d.get("limite") or 10))}
        return fn

    def trecho(self, fn):
        """fn(ref, destino) -> caminho do arquivo (dentro de destino), uns 7 s para ouvir."""
        self._rotas["/fonte/trecho"] = lambda d: {"arquivo": str(fn(d.get("ref"), Path(d["destino"])))}
        return fn

    def obter(self, fn):
        """fn(tarefa, ref, destino, video) -> {"audio", "video"?, "info", "qualidade"?} (arquivos dentro de destino)."""
        self._obter = fn
        return fn

    def reconhecer(self, fn):
        """fn(musica) -> {"ref", "chave"?, "info"?, "contexto"?} ou None (fonte_musicas). O app pergunta pelas
        musicas gravadas por versoes antigas, sem origem: `musica` e o registro dela como esta (o formato antigo
        da sua fonte, so voce conhece). Reconheceu: ela passa a ser sua, no formato de hoje."""
        self._rotas["/fonte/reconhecer"] = lambda d: {"musica": fn(dict(d.get("musica") or {})) or None}
        return fn

    def letras_buscar(self, fn):
        """fn(dados) -> [{"fonte", "id", "titulo", "artista", "album", "duracao", "sincronizada", "palavras"}]."""
        self._rotas["/letras/buscar"] = lambda d: {"resultados": fn(d)}
        return fn

    def letras_obter(self, fn):
        """fn(fonte, id) -> texto da letra (LRC ou texto)."""
        self._rotas["/letras/obter"] = lambda d: {"texto": fn(d.get("fonte"), d.get("id")) or ""}
        return fn

    def capas_buscar(self, fn):
        """fn(texto) -> [{"url", "titulo", "artista", "album", "ano", "genero"}]."""
        self._rotas["/capas/buscar"] = lambda d: {"resultados": fn(str(d.get("texto") or ""))}
        return fn

    # ---------------------------------------------------------------- tarefas
    def _rodar_tarefa(self, t):
        try:
            d = t.dados
            if t.tipo == "acao_musica":
                fn = self._acoes_musica[d["acao"]]
                t.resultado = fn(t, dict(d.get("musica") or {}), Path(d["destino"])) or {}
            else:
                t.resultado = self._obter(t, d.get("ref"), Path(d["destino"]), bool(d.get("video")))
            t.estado = "pronta"
            t.fracao = 1.0
        except Cancelada:
            t.estado = "cancelada"
        except Erro as exc:
            t.estado, t.erro = "erro", exc.mensagem
        except Exception as exc:  # noqa: BLE001
            log.error("tarefa %s falhou:\n%s", t.id, traceback.format_exc())
            t.estado, t.erro = "erro", str(exc)[:500]

    def _nova_tarefa(self, dados):
        tipo = dados.get("tipo", "obter")
        if tipo == "obter" and not self._obter:
            raise Erro("este complemento não obtém músicas", 404)
        if tipo == "acao_musica" and dados.get("acao") not in self._acoes_musica:
            raise Erro(f"ação desconhecida: {dados.get('acao')}", 404)
        if tipo not in ("obter", "acao_musica"):
            raise Erro(f"tarefa desconhecida: {tipo}", 404)
        if not dados.get("destino"):
            raise Erro("falta o destino")
        t = Tarefa(tipo, dados)
        with self._lock:
            # as antigas terminadas saem (o app ja leu)
            for tid in [k for k, v in self._tarefas.items() if v.estado != "rodando" and time.time() - v.em > 3600]:
                self._tarefas.pop(tid, None)
            self._tarefas[t.id] = t
        threading.Thread(target=self._rodar_tarefa, args=(t,), name=f"tarefa-{t.id[:6]}", daemon=True).start()
        return {"tarefa": t.id}

    # --------------------------------------------------------------- servidor
    def _atender(self, metodo, caminho, dados):
        if metodo == "GET" and caminho == "/saude":
            return {"ok": True, "versao": self.manifesto.get("versao"), "api": API}
        if metodo == "POST" and caminho == "/configurar":
            self.opcoes = dict(dados.get("opcoes") or {})
            self.idioma = dados.get("idioma") or self.idioma
            if self._ao_configurar:
                self._ao_configurar(self.opcoes, self.idioma)
            return {"ok": True}
        if metodo == "GET" and caminho == "/estado":
            return self._estado() if self._estado else {"texto": "", "nivel": "ok"}
        m = re.match(r"^/acoes/([a-z0-9_-]+)$", caminho)
        if metodo == "POST" and m:
            fn = self._acoes.get(m.group(1))
            if not fn:
                raise Erro(f"ação desconhecida: {m.group(1)}", 404)
            return fn(dados) or {"texto": ""}
        if metodo == "POST" and caminho == "/tarefas":
            return self._nova_tarefa(dados)
        m = re.match(r"^/tarefas/([0-9a-f]{32})$", caminho)
        if m:
            t = self._tarefas.get(m.group(1))
            if not t:
                raise Erro("tarefa não encontrada", 404)
            if metodo == "DELETE":
                t.cancelada = True
                return {"ok": True}
            return t.publico()
        if metodo == "POST" and caminho in self._rotas:
            return self._rotas[caminho](dados)
        raise Erro(f"rota desconhecida: {metodo} {caminho}", 404)

    def _handler(self):
        comp = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def _responder(self, status, corpo):
                data = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _tratar(self, metodo):
                if not comp.senha or not hmac.compare_digest(self.headers.get("X-Senha") or "", comp.senha):
                    return self._responder(403, {"erro": "senha errada"})
                if self.headers.get("X-Idioma"):
                    comp.idioma = self.headers["X-Idioma"]
                dados = {}
                tamanho = int(self.headers.get("Content-Length") or 0)
                if tamanho:
                    try:
                        dados = json.loads(self.rfile.read(tamanho).decode("utf-8") or "{}")
                    except ValueError:
                        return self._responder(400, {"erro": "JSON inválido"})
                try:
                    self._responder(200, comp._atender(metodo, self.path.split("?", 1)[0], dados))
                except Erro as exc:
                    self._responder(exc.status, {"erro": exc.mensagem})
                except Exception as exc:  # noqa: BLE001
                    log.error("%s %s falhou:\n%s", metodo, self.path, traceback.format_exc())
                    self._responder(500, {"erro": str(exc)[:500]})

            def do_GET(self):  # noqa: N802
                self._tratar("GET")

            def do_POST(self):  # noqa: N802
                self._tratar("POST")

            def do_DELETE(self):  # noqa: N802
                self._tratar("DELETE")

        return Handler

    def rodar(self):
        """Atende o app ate o processo ser encerrado."""
        if not self.senha or not self.porta:
            sys.exit("este programa e um complemento do IOkê: ele e ligado pelo app (faltam "
                     "KARAOKE_COMPLEMENTO_PORTA e KARAOKE_COMPLEMENTO_SENHA)")
        servidor = ThreadingHTTPServer(("127.0.0.1", self.porta), self._handler())
        servidor.daemon_threads = True
        log.info("complemento %s %s na porta %s", self.manifesto.get("id"), self.manifesto.get("versao"), self.porta)
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass
