"""Extraction du texte des PDF OCR D&D 3.5 → .txt avec séparateurs de page.

Les PDF OCR sont issus de `livres/Donjons et Dragons - AD&D - 3.5ème Edition OCR/`.
On émet un .txt par livre, avec le séparateur `===== page NNNN — <stem> =====`
qui correspond au regex `_DRS_PAGE_RE` du chunker (cf. chunker.py). On gagne au
passage une vraie pagination des manuels de base (les anciens .txt utilisaient
`===== PAGE N =====` qui ne matchait PAS le regex et n'étaient donc jamais
découpés en sections — toutes les citations venaient sans n° de page).

Usage :
    py -m server.rag.extract_ocr_books           # extrait les 17 livres
    py -m server.rag.extract_ocr_books --dry    # liste le mapping sans écrire

Le mapping livre → KB/filename est codé en dur ci-dessous. Les 3 manuels de
base préexistants (guide_maitre, manuel_joueur, manuel_monstres) sont sauvegardés
en `.bak` avant écrasement ; errata/faq/DRS ne sont pas touchés.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

try:
    import fitz                              # PyMuPDF
except ImportError:                          # pragma: no cover
    fitz = None

log = logging.getLogger("dnd35.rag.extract")

# Localisation des sources / cibles. Aucune dépendance vers la config YAML —
# ce script est autonome et peut tourner même si ChromaDB n'est pas initialisé.
DEFAULT_OCR_DIR = Path(
    r"C:/Users/alain/OneDrive/Desktop/Dongeon dragon/documentation/livres/"
    r"Donjons et Dragons - AD&D - 3.5ème Edition OCR"
)
DEFAULT_OUT_ROOT = Path(
    r"C:/Users/alain/OneDrive/Desktop/Dongeon dragon/projet_DnD35/knowledge_import"
)

KB1 = "KB1_Manuels_de_base"
KB2 = "KB2_Aide_creation_perso"
KB4 = "KB4_DRS_corpus"


@dataclass(frozen=True)
class Book:
    src_pdf_stem: str        # ex: "Manuel des Joueurs_text"
    kb: str                  # KB1 | KB2 | KB4
    target_stem: str         # ex: "manuel_joueur_3.5"
    replaces: bool = False    # True si un .txt préexistant doit être écrasé


MAPPING: tuple[Book, ...] = (
    # ---------- KB1 — Manuels de base (règles officielles / arbitrage) ----------
    Book("Guide du Maître_text", KB1, "guide_maitre_3.5", replaces=True),
    Book("Manuel des Joueurs_text", KB1, "manuel_joueur_3.5", replaces=True),
    Book("Manuel des Monstres 1_text", KB1, "manuel_monstres_1_3.5", replaces=False),
    Book("Manuel des Monstres 2_text", KB1, "manuel_monstres_2_3.5"),
    Book("Manuel des Monstres 3_text", KB1, "manuel_monstres_3_3.5"),

    # ---------- KB2 — Aide création perso (options / sorts / dons) ----------
    Book("Codex Aventureux_text", KB2, "codex_aventureux_3.5", replaces=True),
    Book("Codex Divin_text", KB2, "codex_divin_3.5"),
    Book("Codex Martial_text", KB2, "codex_martial_3.5"),
    Book("Codex Profane_text", KB2, "codex_profane_3.5"),
    Book("Les Arcanes Exhumés_text", KB2, "arcanes_exhumes_3.5"),
    Book("Les Chapitres Sacrés_text", KB2, "chapitres_sacres_3.5"),
    Book("Grand Manuel des Psioniques_text", KB2, "grand_manuel_des_psioniques_3.5"),

    # ---------- KB4 — Extensions du corpus (lore / univers / bestiaires annexes) ----------
    Book("Draconomicon - Le livre des dragons_text", KB4, "draconomicon_3.5"),
    Book("Eberron - Les Ombres de la Dernière Guerre_text",
         KB4, "eberron_ombres_derniere_guerre_3.5"),
    Book("Eberron - Univers_text", KB4, "eberron_univers_3.5"),
    Book("Libris Mortis_text", KB4, "libris_mortis_3.5"),
    Book("Ravenloft - Livre de règles_text", KB4, "ravenloft_livre_regles_3.5"),
)

# Ancien nom (sans numérotation → obsolète puisqu'on a maintenant MM1/2/3).
LEGACY_TO_DELETE: tuple[tuple[str, str], ...] = (
    (KB1, "manuel_monstres_3.5.txt"),
)


# --------------------------------------------------------------------------- #
#  Nettoyage OCR léger — on corrige seulement les artefacts systèmes, pas les
#  erreurs de reconnaissance (laisser ChromaDB + embeddings absorber le bruit).
# --------------------------------------------------------------------------- #
_WS_LINE_TAIL = re.compile(r"[ \t]+\n")
_WS_RUN = re.compile(r" {3,}")
_BLANK_RUN = re.compile(r"\n{3,}")


def _clean_page(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _WS_LINE_TAIL.sub("\n", t)
    t = _WS_RUN.sub("  ", t)
    t = _BLANK_RUN.sub("\n\n", t)
    return t.strip()


def _ascii_key(s: str) -> str:
    """Clé de comparaison insensible aux accents et à la casse.

    Les noms de fichiers PDF utilisent une normalisation Unicode variable
    (NFC vs NFD) ; on passe en ASCII pour matcher robustesse. Aussi utile
    car le stem source contient Unicode mais le target_stem est ASCII.
    """
    nf = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nf if not unicodedata.combining(c)).lower()


_PDF_PREFIX = "D&D 3.5 - "         # préfixe commun à tous les fichiers OCR


def _find_src_pdf(stem: str, src_dir: Path) -> Path | None:
    """Trouve le PDF OCR par stem fuzzy (préfixe + accents insensibles)."""
    target_key = _ascii_key(stem)
    # On exige aussi « text » à la fin pour ne pas matcher le dossier SCAN.
    candidates = [p for p in src_dir.glob("*.pdf")
                  if _ascii_key(p.name).endswith("_text.pdf")]
    for p in candidates:
        # Retire le préfixe et l'extension pour comparer le stem only.
        core = p.stem
        if core.startswith(_PDF_PREFIX):
            core = core[len(_PDF_PREFIX):]
        if _ascii_key(core).endswith(target_key) or target_key == _ascii_key(core):
            return p
    return None


def extract_book(book: Book, src_dir: Path, out_root: Path, dry: bool) -> int:
    """Retourne le n° de la dernière page extraite (0 si échec)."""
    src_pdf = _find_src_pdf(book.src_pdf_stem, src_dir)
    if src_pdf is None:
        log.error("PDF introuvable (stem=%s)", book.src_pdf_stem)
        if dry:
            log.error("  candidats présents : %s",
                      [p.name for p in src_dir.glob('*.pdf')])
        return 0
    if fitz is None:
        log.error("PyMuPDF requis : `pip install pymupdf`")
        return 0

    out_dir = out_root / book.kb
    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{book.target_stem}.txt"

    if dry:
        print(f"DRY  {book.kb}/{out_txt.name}  ←  {src_pdf.name}")
        return 0

    # Sauvegarde du .txt préexistant si on l'écrase (sécurité de réversibilité).
    if out_txt.is_file():
        bak = out_txt.with_suffix(out_txt.suffix + ".bak")
        shutil.copy2(out_txt, bak)
        log.info("Backup %s → %s", out_txt.name, bak.name)

    doc = fitz.open(str(src_pdf))
    parts: list[str] = []
    for i, page in enumerate(doc, start=1):
        sep = f"===== page {i:04d} — {book.target_stem} ====="
        parts.append(sep)
        body = _clean_page(page.get_text())
        if body:
            parts.append(body)
    doc.close()
    out_txt.write_text("\n".join(parts) + "\n", encoding="utf-8")
    log.info("Extrait %s : %d pages → %s",
             out_txt.name, i, f"{out_txt.stat().st_size // 1024} KiB")
    return i


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="server.rag.extract_ocr_books",
                                     description=__doc__)
    parser.add_argument(
        "--src", default=str(DEFAULT_OCR_DIR),
        help="Dossier des PDF OCR (default: livres/.../OCR)"
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT_ROOT),
        help="Racine knowledge_import (default: projet_DnD35/knowledge_import)"
    )
    parser.add_argument("--dry", action="store_true",
                        help="Liste le mapping sans écrire")
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    if not src.is_dir():
        print(f"Dossier OCR introuvable : {src}", file=sys.stderr)
        return 2
    if not args.dry and not out.is_dir():
        print(f"Racine knowledge_import introuvable : {out}", file=sys.stderr)
        return 2

    print("=== Extraction OCR → .txt (séparateur `===== page NNNN — <stem> =====`) ===")
    print(f"Source : {src}")
    print(f"Cible  : {out}")
    print(f"Livres : {len(MAPPING)} (écrasent {sum(1 for b in MAPPING if b.replaces)} préexistants)")
    print()
    total_pages = 0
    for book in MAPPING:
        last = extract_book(book, src, out, args.dry)
        total_pages += last
    if not args.dry:
        for kb, legacy in LEGACY_TO_DELETE:
            p = out / kb / legacy
            if p.is_file():
                bak = p.with_suffix(p.suffix + ".bak")
                shutil.copy2(p, bak)
                p.unlink()
                log.info("Supprimé (legacy) %s/%s — backup .bak conservé", kb, legacy)
    print()
    print(f"OK : {total_pages} pages extraites au total "
          f"({len(MAPPING)} livres → {KB1}|{KB2}|{KB4}).")
    print("Prochaine étape : `py -m server.rag --ingest --force` "
          "puis `py -m server.rag --stats`.")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
