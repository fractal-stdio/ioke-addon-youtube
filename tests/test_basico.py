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


def test_lists_from_links():
    assert youtube.lista_do_texto("https://www.youtube.com/playlist?list=PLHmguiAhyeHWzYg8XE4wLT_iC0_RC1Wti") == \
        "PLHmguiAhyeHWzYg8XE4wLT_iC0_RC1Wti"
    assert youtube.lista_do_texto("https://www.youtube.com/watch?v=amnD41wlj7Q&list=PLabcdefghij") == "PLabcdefghij"
    assert youtube.lista_do_texto("https://music.youtube.com/browse/MPREb_nHbCAGX6uUL") == "MPREb_nHbCAGX6uUL"
    assert youtube.lista_do_texto("https://youtu.be/dQw4w9WgXcQ") is None
    assert youtube.lista_do_texto("pink floyd list=PLabcdefghij") is None  # so em link do YouTube
    assert youtube.url_da_lista("MPREb_nHbCAGX6uUL") == "https://music.youtube.com/browse/MPREb_nHbCAGX6uUL"
    assert youtube.url_da_lista("PLabcdefghij") == "https://www.youtube.com/playlist?list=PLabcdefghij"


def test_album_name_and_cover():
    assert youtube.nome_do_album({"id": "OLAK5uy_x", "title": "Album - The Dark Side of the Moon"}) == \
        (True, "The Dark Side of the Moon")
    assert youtube.nome_do_album({"id": "PLx", "title": "Minha playlist"}) == (False, "Minha playlist")
    info = {"thumbnails": [{"url": "a", "width": 120, "height": 90}, {"url": "b", "width": 480, "height": 360}]}
    assert youtube.capa_da_lista(info) == "b"
    assert youtube.capa_da_lista({"entries": [{"id": "dQw4w9WgXcQ"}]}).endswith("/dQw4w9WgXcQ/mqdefault.jpg")


def test_list_songs(monkeypatch):
    """Album: nome da faixa, artista, album e numero; playlist comum: o palpite pelo titulo; privados ficam de fora."""
    yt = youtube.YouTube()
    album = {"id": "OLAK5uy_x", "title": "Album - Disco", "entries": [
        {"id": "aaaaaaaaaaa", "title": "Faixa Um", "channel": "Banda", "duration": 100},
        {"id": "bbbbbbbbbbb", "title": "[Private video]"},
        {"id": "ccccccccccc", "title": "Faixa Dois", "channel": "Banda", "duration": 200}]}
    monkeypatch.setattr(yt, "info_da_lista", lambda ref, ate=0: album)
    r = yt.lista("MPREb_xyz123456")
    assert (r["titulo"], r["artista"]) == ("Disco", "Banda")
    assert [(i["titulo"], i["album"], i["faixa"]) for i in r["itens"]] == [("Faixa Um", "Disco", 1), ("Faixa Dois", "Disco", 2)]
    playlist = {"id": "PLx", "title": "Festa", "channel": "Eu", "entries": [
        {"id": "aaaaaaaaaaa", "title": "Legião Urbana - Tempo Perdido (Clipe Oficial) Lyrics", "channel": "Canal Qualquer"}]}
    monkeypatch.setattr(yt, "info_da_lista", lambda ref, ate=0: playlist)
    it = yt.lista("PLxxxxxxxxxx")["itens"][0]
    assert (it["artista"], it["titulo"], it.get("faixa")) == ("Legião Urbana", "Tempo Perdido", None)
    assert it["ref"] == it["chave"] == "aaaaaaaaaaa"  # a mesma musica adicionada sozinha: o mesmo id no IOkê


def test_trailing_junk_in_names():
    from nomes import clean_track

    assert clean_track("Speak to Me (HQ) Lyrics") == "Speak to Me"
    assert clean_track("Tempo Perdido Clipe Oficial") == "Tempo Perdido"
    assert clean_track("Video Killed the Radio Star") == "Video Killed the Radio Star"
    assert clean_track("Lyrics") == "Lyrics"  # o nome todo: fica


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
