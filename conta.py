"""Conta do YouTube (ex.: Premium), para baixar o audio na melhor qualidade.

O login:
  1. Abre o Chrome/Edge com um PERFIL SEPARADO (na pasta de dados do complemento), so para isso,
     na pagina de login do Google. A senha e digitada no site do Google, nunca aqui.
  2. Enquanto a janela esta aberta, le os cookies da sessao pela porta de depuracao local do
     navegador (Chrome DevTools Protocol): ler o arquivo de cookies do Chrome nao funciona mais
     (desde 2024 ele e criptografado de um jeito que outros programas nao abrem).
  3. Quando o YouTube marca a conta como logada, grava os cookies (formato do yt-dlp) e fecha a janela.
"""
import http.cookiejar
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("complemento.conta")

LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&hl={hl}"
             "&continue=https%3A%2F%2Fwww.youtube.com%2F%3Fhl%3D{hl}")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
VIDEO_TESTE = "dQw4w9WgXcQ"  # so para ver quais formatos a conta libera
LIMITE = 15 * 60


def achar_navegador():
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for nome, p in (("Google Chrome", Path(local, "Google/Chrome/Application/chrome.exe")),
                    ("Google Chrome", Path(pf, "Google/Chrome/Application/chrome.exe")),
                    ("Google Chrome", Path(pf86, "Google/Chrome/Application/chrome.exe")),
                    ("Microsoft Edge", Path(pf86, "Microsoft/Edge/Application/msedge.exe")),
                    ("Microsoft Edge", Path(pf, "Microsoft/Edge/Application/msedge.exe"))):
        if p.exists():
            return nome, str(p)
    return None


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cdp(ws_url, metodo, params=None, tempo=10):
    """Um comando no navegador (Chrome DevTools Protocol)."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    with connect(ws_url, max_size=None, open_timeout=5) as ws:
        ws.send(json.dumps({"id": 1, "method": metodo, "params": params or {}}))
        fim = time.time() + tempo
        while time.time() < fim:
            try:
                msg = json.loads(ws.recv(timeout=max(0.1, fim - time.time())))
            except ConnectionClosed:
                return {}
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "erro do navegador"))
                return msg.get("result", {})
    raise TimeoutError(metodo)


def _logado(cookies):
    nomes = {c["name"] for c in cookies if c.get("domain", "").endswith("youtube.com")}
    return "LOGIN_INFO" in nomes or {"SAPISID", "__Secure-3PSID"} <= nomes


class Conta:
    def __init__(self, dados, youtube, idioma=lambda: "en"):
        self.dados = Path(dados)
        self.cookies_arquivo = self.dados / "youtube-cookies.txt"
        self.estado_arquivo = self.dados / "youtube-account.json"
        self.perfil = self.dados / "youtube-login"
        self.youtube = youtube
        self.idioma = idioma
        self.lock = threading.Lock()
        self.fase = "parado"  # parado | esperando | conferindo | pronto | erro
        self.mensagem = ""
        self.proc = None
        self._cancelar = False
        try:
            self.status = json.loads(self.estado_arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.status = None

    def cookies(self):
        """O arquivo de cookies da conta (ou None se a ultima conferencia disse que nao vale)."""
        if not (self.cookies_arquivo.exists() and self.cookies_arquivo.stat().st_size > 0):
            return None
        return None if (self.status or {}).get("logged_in") is False else self.cookies_arquivo

    # ------------------------------------------------------------ login
    def entrar(self):
        with self.lock:
            if self.fase in ("esperando", "conferindo"):
                return
            nav = achar_navegador()
            if not nav:
                raise RuntimeError("chrome")
            porta = _porta_livre()
            self.perfil.mkdir(parents=True, exist_ok=True)
            hl = "pt-BR" if self.idioma() == "pt-BR" else "en"
            self._cancelar = False
            self.proc = subprocess.Popen([nav[1], f"--remote-debugging-port={porta}", f"--user-data-dir={self.perfil}",
                                          "--no-first-run", "--no-default-browser-check", "--new-window",
                                          LOGIN_URL.format(hl=hl)])
            self.fase, self.mensagem = "esperando", nav[0]
            threading.Thread(target=self._vigiar, args=(porta,), daemon=True).start()

    def _parar_navegador(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _gravar_cookies(self, cookies):
        linhas = ["# Netscape HTTP Cookie File", "# NAO compartilhe: da acesso a sua conta.", ""]
        longe = int(time.time()) + 30 * 86400
        for c in cookies:
            dominio = c.get("domain", "")
            if not (dominio.endswith("youtube.com") or dominio.endswith("google.com")):
                continue
            expira = int(c.get("expires") or -1)
            linhas.append("\t".join([dominio, "TRUE" if dominio.startswith(".") else "FALSE", c.get("path") or "/",
                                     "TRUE" if c.get("secure") else "FALSE", str(expira if expira > 0 else longe),
                                     c["name"], c.get("value", "")]))
        tmp = self.cookies_arquivo.with_name(self.cookies_arquivo.name + ".tmp")
        tmp.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        os.replace(tmp, self.cookies_arquivo)

    def _vigiar(self, porta):
        fim = time.time() + LIMITE
        ws_url = None
        try:
            while time.time() < fim:
                if self._cancelar:
                    self.fase, self.mensagem = "parado", ""
                    return
                if self.proc.poll() is not None:
                    self.fase, self.mensagem = "erro", "fechada"
                    return
                try:
                    if not ws_url:
                        ws_url = requests.get(f"http://127.0.0.1:{porta}/json/version", timeout=2).json()[
                            "webSocketDebuggerUrl"]
                    cookies = _cdp(ws_url, "Storage.getCookies").get("cookies", [])
                except Exception:  # noqa: BLE001 - o navegador ainda esta abrindo
                    time.sleep(1.5)
                    continue
                if _logado(cookies):
                    time.sleep(3)  # deixa o YouTube terminar de gravar a sessao
                    cookies = _cdp(ws_url, "Storage.getCookies").get("cookies", cookies)
                    self._gravar_cookies(cookies)
                    try:
                        _cdp(ws_url, "Browser.close", tempo=3)
                    except Exception:  # noqa: BLE001
                        pass
                    self._conferir()
                    return
                time.sleep(1.5)
            self.fase, self.mensagem = "erro", "demorou"
        except Exception as exc:  # noqa: BLE001
            log.exception("login falhou")
            self.fase, self.mensagem = "erro", str(exc)[:200]
        finally:
            if self.fase != "esperando":
                self._parar_navegador()

    # ------------------------------------------------------------ conferir
    def conferir(self):
        if not self.cookies_arquivo.exists():
            return False
        threading.Thread(target=self._conferir, daemon=True).start()
        return True

    def _conferir(self):
        self.fase = "conferindo"
        try:
            jar = http.cookiejar.MozillaCookieJar(str(self.cookies_arquivo))
            jar.load(ignore_discard=True, ignore_expires=True)
            s = requests.Session()
            s.cookies = jar
            s.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
            html = s.get("https://www.youtube.com/?hl=pt-BR", timeout=20).text
            logado = '"LOGGED_IN":true' in html
            logo = re.search(r'"topbarLogoRenderer":\{"iconImage":\{"iconType":"([A-Z_]+)"', html)
            premium = ("PREMIUM" in logo.group(1)) if (logo and logado) else None
            nome = email = None
            try:
                r = s.get("https://www.youtube.com/getAccountSwitcherEndpoint", timeout=15)
                t = r.text[4:] if r.text.startswith(")]}'") else r.text
                m = re.search(r'"accountName":\{"simpleText":"([^"]+)"', t)
                nome = m.group(1) if m else None
                m = re.search(r'"accountByline":\{"simpleText":"([^"]+)"', t)
                email = m.group(1) if (m and "@" in m.group(1)) else None
            except Exception:  # noqa: BLE001 - o nome e so enfeite
                pass
            melhor = None
            if logado:
                try:
                    melhor = self.youtube.melhor_audio(VIDEO_TESTE, com_conta=True)
                except Exception as exc:  # noqa: BLE001
                    log.info("teste de formatos com a conta falhou: %s", exc)
            self.status = {"logged_in": logado, "premium": premium, "name": nome, "email": email,
                           "best_audio": melhor, "checked_at": time.time()}
            self.estado_arquivo.write_text(json.dumps(self.status, ensure_ascii=False), encoding="utf-8")
            self.fase, self.mensagem = ("pronto", "") if logado else ("erro", "nao_reconheceu")
        except Exception as exc:  # noqa: BLE001
            log.exception("conferir a conta falhou")
            self.fase, self.mensagem = "erro", str(exc)[:200]

    def sair(self):
        self._cancelar = True
        self._parar_navegador()
        for p in (self.cookies_arquivo, self.estado_arquivo):
            p.unlink(missing_ok=True)
        shutil.rmtree(self.perfil, ignore_errors=True)
        self.status = None
        self.fase, self.mensagem = "parado", ""
