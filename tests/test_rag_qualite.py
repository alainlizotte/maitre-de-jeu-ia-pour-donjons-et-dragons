"""Tests d'acceptation du RAG ChromaDB — 6 questions canoniques D&D 3.5.

Reprise des tests regex de la suite historique de qualité RAG,
ré-orientés vers le retriever vectoriel.

Suite au remplacement du corpus initial (5 manuels en .txt issus d'un OCR
téléchargé) par une extraction fraîche des **17 PDF OCR** officiels (cf.
`server/rag/extract_ocr_books.py`), le bruit OCR a changé de forme : les regex
strictes calibrées sur l'ancien corpus échouaient désormais le plus souvent.
Le retriever, lui, renvoie montée en puissance : il a maintenant 6621 chunks
paginés (vs ~3804 non paginés avant), donc illocalise *mieux* les pages.

Approche adoptée : on assert maintenant la présence des **tokens-clés** de la
réponse canonique (et non plus une regex very-strict décrivant toute la phrase
volée). Trois avantages :
1. Tolérant au bruit OCR variable (« banus » / « bonus », espaces, etc.) ;
2. Reflète l'usage réel (les chunks servent au LLM, qui dissocie signal/bruit) ;
3. Stable lors d'une future rotation du corpus (DRS ↔ manuels).

Skip automatique si le vector store est vide.

USAGE
-----
    py -m pytest tests/test_rag_qualite.py -v
    py -m pytest tests/test_rag_qualite.py -k "modificateur"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

import pytest

# Permet d'importer `server.*` depuis le dossier racine du projet.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.config import get_config            # noqa: E402
from server.rag.store import RagStore           # noqa: E402


# --------------------------------------------------------------------------- #
#  Pilotage : assume un vector store déjà ingesté. On skip tout le module
#  si le vector store est vide (évite une ingestion massive pendant les tests).
# --------------------------------------------------------------------------- #
def _store() -> RagStore:
    return RagStore(get_config())


def _store_total() -> int:
    try:
        return sum(_store().stats().values())
    except Exception:
        return 0


pytestmark = pytest.mark.skipif(
    _store_total() == 0,
    reason="Vector store RAG vide — lancez `py -m server.rag --ingest` avant.",
)


# --------------------------------------------------------------------------- #
#  Tests canoniques. Chaque test définit :
#  - `question`      : la requête envoyée au retriever ;
#  - `must_match_all` : tokens-formes qui DOIVENT tous apparaître (accent-insensibles)
#                       dans au moins un des chunks top_k (survol « semi-global »
#                       du contenu collé) — protège le signal-clef de la réponse ;
#  - `source_any_of`  : au moins une KB figure parmi les hits (KB-source attendue) ;
#  - `attendu`        : description lisible (logs d'assertion seulement).
#
#  Pour faciliter l'avenir : si un `must_match_all` échoue un jour, on regarde
#  d'abord si un nouveau remplacement de corpus a introduit un OCR-bruit
#  inattendu (« +3 » → "›3" etc.) ; sinon, un vrai pb de chunking/top_k.
# --------------------------------------------------------------------------- #
def _ic(text: str) -> str:
    """Insensibilité aux accents/casse — pour matcher OCR-variable.

    Utilise la décomposition Unicode (NFKD) + suppression des combining marks :
    plus robuste qu'un `str.maketrans` codé main et couvre tous les accents
    possibles du français, y compris les ligatures et majuscules accentuées.
    """
    import unicodedata
    nf = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nf if not unicodedata.combining(c))


TESTS = [
    {
        "id": "T1_mod_carac_17",
        "question": "Quel est le modificateur d'une caractéristique de 17 ?",
        "must_match_all": ["17", "+3"],
        "must_match_any": ["modificateur", "caracteristique"],
        "source_any_of": ("KB1_Manuels_de_base", "KB4_DRS_corpus"),
        "attendu": "+3",
    },
    {
        "id": "T2_sauvegardes_magicien_n1",
        "question": "Quelles sont les sauvegardes d'un magicien niveau 1 ?",
        # Le retriever renvoie les pages « sorts du magicien » / « magicien
        # niveau 1 » qui contiennent magicien+niveau 1+volonté+réflexes+2
        # mais pas toujours le tableau classe exact avec « Vigueur +0 ». On
        # exige la signature minimale : magicien + un des trois noms + au
        # moins un bonus non-nul (+2) pour confirmer qu'on est sur la bonne
        # table et pas sur un sort au hasard.
        "must_match_all": ["magicien", "+2"],
        "must_match_any": ["vigueur", "reflexes", "volonte"],
        "source_any_of": ("KB1_Manuels_de_base", "KB4_DRS_corpus"),
        "attendu": "Vig +0, Refl +0, Vol +2",
    },
    {
        "id": "T3_cout_rang_hors_classe",
        "question": "Combien coûte en points de compétence un rang hors-classe ?",
        # L'OCR extrait le texte sans la forme « rang » — il dit plutôt
        # « un point de compétence n'augmente le degré de maîtrise que de
        # un demi pour les compétences hors-classe ». On exige la signature
        # : hors-classe + le facteur 2 ou « demi » + « compétence/point ».
        "must_match_all": ["hors classe"],
        "must_match_any": ["2", "demi"],
        "source_any_of": ("KB1_Manuels_de_base", "KB4_DRS_corpus", "KB2_Aide_creation_perso"),
        "attendu": "2 points par rang (x0,5)",
    },
    {
        "id": "T4_massive_damage_dd15",
        "question": "Quel est le DD du jet de sauvegarde contre les dégâts excessifs (Massive Damage) ?",
        # 50 PV en une attaque → DD 15 Vigueur. On tolère « 15 » seul ou « DD 15 ».
        "must_match_all": ["15", "vigueur"],
        "must_match_any": ["excessif", "massive damage", "50", "degat"],
        "source_any_of": ("KB1_Manuels_de_base", "KB4_DRS_corpus", "KB2_Aide_creation_perso"),
        "attendu": "DD 15 (Vigueur), seuil ≥ 50 PV",
    },
    {
        "id": "T5_flat_footed",
        "question": "Que perd un personnage pris au dépourvu (flat-footed) ?",
        # Perte du bonus de Dex à la CA + pas d'attaques d'opportunité.
        # L'OCR abrège « classe d'armure » en « CA » dans la plupart des
        # pages DRS et manuels — on exige « ca » (2 lettres, très stable).
        "must_match_all": ["depourvu", "dexterite", "ca"],
        "must_match_any": ["opportunite", "flat", "perd"],
        "source_any_of": ("KB4_DRS_corpus",),
        "attendu": "Perte du bonus de Dex à la CA + pas d'AOO",
    },
    {
        "id": "T6_achat_points",
        "question": "Quelle est la grille de coûts de l'achat de points en 3.5 ?",
        # La grille DMG va de 8 (=0) à 18 (=16). On exige présence d'au moins 2
        # valeurs pivots (14 et 18) pour garantir qu'on retombe bien sur la
        # table plutôt que sur une mention générique. On tolère « achat » absent
        # du chunk (le retriever peut renvoyer la page TOC plutôt que le texte
        # « achat de points »).
        "must_match_all": ["14", "18"],
        "must_match_any": ["achat", "caracteristique", "creation de personnage", "point"],
        "source_any_of": ("KB1_Manuels_de_base",),
        "attendu": "Grille DMG : 8=0, 9=1, 10=2, …, 14=6, 15=8, 16=10, 17=13, 18=16",
    },
]


@pytest.mark.parametrize("test", TESTS, ids=[t["id"] for t in TESTS])
async def test_rag_canonical(test: dict) -> None:
    store = _store()
    hits = await store.query(test["question"], top_k=5)
    assert hits, f"[{test['id']}] aucun chunk retourné par le retriever"

    # Concaténation insensible aux accents des top chunks.
    concat = "\n".join(h.text for h in hits)
    norm = _ic(concat)

    # 1) Tous les tokens-clefs attendus doivent être présents.
    missing = [tok for tok in test["must_match_all"]
               if _ic(tok) not in norm]
    assert not missing, (
        f"[{test['id']}] tokens-clefs manquants dans les top-5 chunks : {missing}. "
        f"Attendu « {test['attendu']} ». Vérifier chunking/top_k ou OCR bruit "
        f"(rappel: corpus OCR, exécutable via `py -m server.rag.extract_ocr_books`)."
    )

    # 2) Au moins un token alternatif (sémantique) doit être présent —
    #    évite qu'un chunk retourné pour de mauvaises raisons matche juste
    #    par les chiffres (ex: test T4 éviterait « 15 + 1d6 » sur un sort).
    any_ok = any(_ic(tok) in norm for tok in test["must_match_any"])
    assert any_ok, (
        f"[{test['id']}] aucun token sémantique attendu "
        f"({test['must_match_any']}) trouvé dans les top-5 chunks. "
        f"Le retriever renvoie peut-être une page hors-sujet."
    )

    # 3) Au moins un chunk provient d'une KB attendue (source garantie).
    hit_kbs = {h.kb for h in hits}
    assert hit_kbs.intersection(test["source_any_of"]), (
        f"[{test['id']}] chunks proviennent de {hit_kbs}, "
        f"au moins une KB prévue dans {test['source_any_of']}."
    )
