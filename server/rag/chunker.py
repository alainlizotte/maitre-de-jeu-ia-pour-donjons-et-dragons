"""RAG — découpage du corpus D&D 3.5 en chunks vectorisables.

Respecte la convention des corpus DRS qui utilisent des séparateurs de page
explicites `===== page NNNN — Titre de la page =====` (cf.
`organize_kb.py` du projet source). Ces séparateurs sont des frontières
dures : on ne coupe jamais au milieu d'une page DRS.

Estimation tokens : ~5 chars/token en français (ratio empirique pour les
manuels OCR-és). C'est volontairement approximatif — ChromaDB n'exige pas
une précision parfaite, et seules les bornes servent à borner la similarité.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# ~5 chars par token en français (manuels OCR). On reste un peu conservateur.
CHARS_PER_TOKEN = 5

# Séparateur de page DRS : `===== page 0045 — Titre de la page =====`.
_DRS_PAGE_RE = re.compile(r"^===== page (\d{1,5})\s*[—-]\s*(.+?)\s*=====\s*$")


@dataclass
class Chunk:
    text: str
    kb: str                          # "KB1_Manuels_de_base" | "KB2_..." | "KB4_..."
    file: str                        # nom sans .txt (ex: "manuel_joueur_3.5")
    page: str = ""                   # n° de page DRS le cas échéant
    title: str = ""                  # titre de page DRS le cas échéant
    index: int = 0                   # 0-based index du chunk dans le fichier

    def metadata(self) -> dict[str, Any]:
        return {"kb": self.kb, "file": self.file, "page": self.page, "title": self.title}


# --------------------------------------------------------------------------- #
#  Découpage
# --------------------------------------------------------------------------- #
def _approx_tokens(text: str) -> int:
    """Estimation grossière du nombre de tokens (≈ chars / 5)."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_pages_drs(content: str) -> list[tuple[str, str]]:
    """Découpe un corpus DRS en sections `[(page_num, titre), texte...]`.

    Renvoie une liste de `(page_info, body)` où page_info vaut "page NNNN — Titre"
    (ou "" si le fichier n'est pas au format DRS avec séparateurs). Le body qui
    précède le premier séparateur est rattaché à une section "" (préambule).
    """
    lines = content.split("\n")
    sections: list[tuple[str, str]] = []
    cur_page_info: str = ""
    cur_buf: list[str] = []
    for line in lines:
        m = _DRS_PAGE_RE.match(line)
        if m:
            # On flush le buffer courant dans la section précédente.
            sections.append((cur_page_info, "\n".join(cur_buf).strip()))
            page_num, title = m.group(1), m.group(2).strip()
            cur_page_info = f"page {page_num} — {title}"
            cur_buf = []
        else:
            cur_buf.append(line)
    # Flush final.
    sections.append((cur_page_info, "\n".join(cur_buf).strip()))
    # Retire les sections vides (fréquentes en tête de fichier).
    return [(pi, b) for (pi, b) in sections if b]


def _parse_page_info(page_info: str) -> tuple[str, str]:
    """Extrait (numéro de page, titre) depuis « page 0045 — Titre »."""
    if not page_info:
        return "", ""
    m = re.match(r"^page (\d{1,5})\s*[—-]\s*(.+)$", page_info)
    if m:
        return m.group(1), m.group(2).strip()
    return "", page_info


def _split_paragraphs(text: str) -> list[str]:
    """Découpe en paragraphes (double newline). Préserve les listes/hdrs."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in parts if p]


def _split_sentences(text: str) -> list[str]:
    """Découpe en phrases (sur ponctuation française + newline de bullet)."""
    # Préserve les listes et titres courts sans splitter agressivement.
    sents = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", text)
    return [s.strip() for s in sents if s.strip()]


def _grow_until_budget(
    pieces: list[str], start: int, max_chars: int
) -> tuple[list[str], int]:
    """Accumule `pieces[start:]` jusqu'à dépasser `max_chars`.

    Renvoie (pieces_collectées, index_suivant). On ne coupe jamais au milieu
    d'une phrase — la borne logique est le paragraphe ou la phrase.
    """
    out: list[str] = []
    total = 0
    i = start
    while i < len(pieces):
        piece = pieces[i]
        if total and total + len(piece) + 1 > max_chars:
            break
        out.append(piece)
        total += len(piece) + 1
        i += 1
    if not out and pieces:                # sécurité : au moins 1 pièce
        out.append(pieces[start])
        i = start + 1
    return out, i


def _chunk_section(
    body: str,
    kb: str,
    file: str,
    page: str,
    title: str,
    chunk_max_chars: int,
    overlap_chars: int,
) -> list[Chunk]:
    """Découpe UNE section (page DRS ou chunk libre pour KB1/KB2) en chunks.

    Hiérarchie : paragraphes → phrases. Chaque chunk accumule jusqu'à budget,
    avec un chevauchement mesuré en chars entre chunks consécutifs.
    """
    chunks: list[Chunk] = []
    paragraphs = _split_paragraphs(body)

    # 1er niveau : si un paragraphe dépasse déjà le budget, on le finesplit en phrases.
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_max_chars:
            pieces.append(para)
        else:
            for sent in _split_sentences(para):
                pieces.append(sent)

    if not pieces:
        return chunks

    i = 0
    idx = 0
    n = len(pieces)
    while i < n:
        acc, nxt = _grow_until_budget(pieces, i, chunk_max_chars)
        text = "\n\n".join(acc).strip()
        if text:
            chunks.append(Chunk(
                text=text, kb=kb, file=file, page=page, title=title, index=idx,
            ))
            idx += 1
        if nxt == i:                       # sécurité anti-boucle
            nxt = i + 1
        # Chevauchement : on recule pour empiéter sur les dernières pièces.
        if overlap_chars > 0 and nxt < n:
            used_chars = sum(len(p) for p in acc)
            # On recule d'environ overlap_chars en remontant les pièces.
            back = 0
            j = nxt
            while j > i + 1 and back < overlap_chars:
                j -= 1
                back += len(pieces[j]) + 2
            i = j
        else:
            i = nxt
    return chunks


def chunk_file(
    path: Path,
    kb: str,
    chunk_size_tokens: int = 1500,
    chunk_overlap_tokens: int = 200,
) -> list[Chunk]:
    """Découpe UN fichier .txt en chunks, en respectant les conventions DRS.

    Le paramètre `kb` est le nom du dossier racine (« KB1_Manuels_de_base »,
    « KB2_Aide_creation_perso » ou « KB4_DRS_corpus »). Il est propagé en
    metadata pour préserver l'étiquetage officiel/narration.
    """
    file_stem = path.stem
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")

    chunk_max_chars = chunk_size_tokens * CHARS_PER_TOKEN
    overlap_chars = chunk_overlap_tokens * CHARS_PER_TOKEN

    sections = _split_pages_drs(content)
    chunks: list[Chunk] = []
    for page_info, body in sections:
        page, title = _parse_page_info(page_info)
        chunks.extend(_chunk_section(
            body=body, kb=kb, file=file_stem,
            page=page, title=title,
            chunk_max_chars=chunk_max_chars, overlap_chars=overlap_chars,
        ))
    # Re-indexe globalement par fichier (cohérence ChromaDB ids).
    for idx, c in enumerate(chunks):
        c.index = idx
    return chunks


def iter_corpus_files(source_dir: Path) -> Iterator[tuple[Path, str]]:
    """Itère `(.txt, kb_name)` sur tous les fichiers du corpus source.

    On parcourt les 3 sous-dossiers `KB*/` connus ; ignore le reste.
    """
    kb_dirs = [
        "KB1_Manuels_de_base",
        "KB2_Aide_creation_perso",
        "KB4_DRS_corpus",
    ]
    for kb in kb_dirs:
        kbdir = source_dir / kb
        if not kbdir.is_dir():
            continue
        for txt in sorted(kbdir.glob("*.txt")):
            yield txt, kb
