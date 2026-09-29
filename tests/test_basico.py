"""O que da para testar sem internet: o manifesto, os resultados e as mensagens de erro."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import youtube  # noqa: E402


def test_manifest():
    m = json.loads((RAIZ / "karaoke-complemento.json").read_text(encoding="utf-8"))
    assert m["id"] == "youtube" and m["api"] == 1 and m["oferece"] == ["fonte_musicas"]
    assert (RAIZ / m["entrada"]).is_file() and (RAIZ / m["dependencias"]).is_file()


def test_result_key_is_the_video_id():
    r = youtube.resultado({"id": "dQw4w9WgXcQ", "title": "Música (Official Video)", "channel": "Canal"})
    assert r["ref"] == r["chave"] == "dQw4w9WgXcQ" and r["tem_trecho"]


def test_guess_and_ids():
    assert youtube.adivinhar({"title": "Legião Urbana - Tempo Perdido (Clipe Oficial)"}) == {
        "artista": "Legião Urbana", "titulo": "Tempo Perdido", "album": None}
    assert youtube.video_id_do_texto("https://youtu.be/dQw4w9WgXcQ?t=3") == "dQw4w9WgXcQ"
    assert youtube.video_id_do_texto("tempo perdido") is None


def test_no_gpl_dependency():
    linhas = [ln.split("#")[0].strip().lower() for ln in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    assert not any(ln.startswith("mutagen") or "[default]" in ln for ln in linhas)


def test_song_actions_are_declared_and_implemented():
    m = json.loads((RAIZ / "karaoke-complemento.json").read_text(encoding="utf-8"))
    ids = {a["id"] for a in m["acoes_musica"]}
    assert ids == {"baixar_de_novo", "baixar_video"}
    codigo = (RAIZ / "complemento.py").read_text(encoding="utf-8")
    assert all(f'@comp.acao_musica("{i}")' in codigo for i in ids)


def test_video_quality_option():
    m = json.loads((RAIZ / "karaoke-complemento.json").read_text(encoding="utf-8"))
    op = next(o for o in m["opcoes"] if o["id"] == "qualidade_video")
    assert [v["id"] for v in op["valores"]] == ["360", "480", "720", "1080"] and op["padrao"] == "720"
    assert "height<=1080" in youtube.formato_video(1080) and "height<=360" in youtube.formato_video("360")


def test_reads_old_and_new_songs(tmp_path, monkeypatch):
    """As musicas antigas (youtube_id / yt, de antes dos complementos) sao reconhecidas e viram o formato de hoje;
    as acoes aceitam a ref pura, o link ou a chave."""
    monkeypatch.setenv("KARAOKE_COMPLEMENTO_DADOS", str(tmp_path))
    import complemento

    antiga = {"id": "abc", "title": "Artista - Musica", "artist": "Artista", "duration": 200,
              "youtube_id": "dQw4w9WgXcQ", "yt": {"title": "Musica (ao vivo)", "channel": "Canal", "description": "d"}}
    r = complemento.reconhecer(antiga)
    assert r["ref"] == r["chave"] == "dQw4w9WgXcQ" and r["info"]["tem_video"] and r["info"]["duracao"] == 200
    assert r["contexto"] == {"title": "Musica (ao vivo)", "channel": "Canal", "description": "d"}
    assert complemento.reconhecer({"url": "https://youtu.be/dQw4w9WgXcQ"})["ref"] == "dQw4w9WgXcQ"
    assert complemento.reconhecer({"title": "arquivo.mp3"}) is None  # nao e deste complemento
    for ref, chave in (("dQw4w9WgXcQ", None), (None, "dQw4w9WgXcQ"), ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
                       (None, "youtube:dQw4w9WgXcQ")):
        assert complemento.id_do_video(ref, chave) == "dQw4w9WgXcQ"
