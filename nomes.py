"""Limpar titulos de videos: "(Official Video)", "[Clipe Oficial]", "feat."..."""
import re


_JUNK_RE = re.compile(
    r"\b(official|oficial|music video|video|vídeo|clipe|clip|lyrics?|letra|legendado|tradução|"
    r"traducao|audio|áudio|hd|hq|4k|remaster(?:ed)?|visualizer|pseudo|ao vivo|live|mv|"
    r"videoclipe|com letra|with lyrics)\b",
    re.I,
)


def _is_junk(segment):
    """True se o trecho e so penduricalho ('Clipe Oficial', 'Official Video'...)."""
    residue = re.sub(r"[\W_]+", "", _JUNK_RE.sub("", segment))
    return len(residue) <= 1


def clean_track(title):
    """Remove (Official Video), [Clipe Oficial], feat., etc."""
    t = title or ""
    t = re.sub(r"[\(\[\{【][^\)\]\}】]*[\)\]\}】]", " ", t)
    t = re.sub(r"\s+(?:ft\.?|feat\.?|part\.?|participação)\s+.*$", "", t, flags=re.I)
    t = re.sub(r"\s*\|.*$", "", t)
    t = re.sub(r"#\w+", "", t)
    segs = [s for s in re.split(r"\s+[-–—]\s+", t) if s.strip()]
    kept = [s for s in segs if not _is_junk(s)]
    t = " - ".join(kept or segs[:1])
    t = re.sub(r"\s{2,}", " ", t)
    # penduricalho solto no fim ("Time Lyrics", "Cais HD", "Tempo Perdido Clipe Oficial"), deixando o nome
    sem_fim = _FIM_RE.sub("", t)
    if sem_fim.strip(" -–—_\"'"):
        t = sem_fim
    return t.strip(" -–—_\"'")


_FIM_RE = re.compile(
    r"(?:\s+(?:official|oficial|music|video|vídeo|clipe|clip|lyrics?|letra|legendado|hd|hq|4k|audio|áudio|"
    r"visualizer|mv|videoclipe))+\s*$",
    re.I,
)


def clean_display_title(title):
    """Titulo para exibir: tira so os penduricalhos tipo (Official Video)."""
    t = title or ""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", lambda m: " " if _JUNK_RE.search(m.group(0)) else m.group(0), t)
    segs = [s for s in re.split(r"\s+[-–—|]\s+", t) if s.strip()]
    kept = [s for s in segs if not _is_junk(s)]
    t = " - ".join(kept or segs[:1])
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -–—_|")


def clean_channel(channel):
    c = channel or ""
    c = re.sub(r"\s*-\s*Topic$", "", c, flags=re.I)
    c = re.sub(r"VEVO$", "", c)
    c = re.sub(r"\b(oficial|official|canal)\b", "", c, flags=re.I)
    return re.sub(r"\s{2,}", " ", c).strip(" -")
