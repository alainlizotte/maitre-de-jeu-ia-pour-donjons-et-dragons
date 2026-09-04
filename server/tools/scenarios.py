"""Outil Scénarios — catalogue structuré par univers.

Charge `data/scenarios_catalogue.json` : chaque univers contient des
scénarios avec PDF, cartes, artwork, objets, enigmes, annexes.
Le texte d'un PDF est extrait à la demande via PyMuPDF (cache mémoire,
plafonné en caractères) pour que le MJ puisse mener l'aventure.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


_CATALOGUE_CACHE: Optional[dict[str, Any]] = None
_FLAT_CACHE: Optional[list[dict[str, Any]]] = None


def _now_iso() -> str:
    return datetime.now().isoformat()


def _catalogue_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "scenarios_catalogue.json")


def charger_catalogue(ctx: ToolContext) -> dict[str, Any]:
    """Charge le catalogue structuré (cache mémoire)."""
    global _CATALOGUE_CACHE
    if _CATALOGUE_CACHE is not None:
        return _CATALOGUE_CACHE
    path = _catalogue_path(ctx)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "universes" in data:
            _CATALOGUE_CACHE = data
            return _CATALOGUE_CACHE
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    _CATALOGUE_CACHE = {"universes": []}
    return _CATALOGUE_CACHE


def _charger_catalogue_plat(ctx: ToolContext) -> list[dict[str, Any]]:
    """Catalogue plat (tous scénarios de tous univers) pour les tools LLM."""
    global _FLAT_CACHE
    if _FLAT_CACHE is not None:
        return _FLAT_CACHE
    cata = charger_catalogue(ctx)
    flat: list[dict[str, Any]] = []
    for u in cata.get("universes", []):
        for s in u.get("scenarios", []):
            s2 = dict(s)
            s2["_univers"] = u.get("nom", "")
            s2["_univers_id"] = u.get("id", "")
            flat.append(s2)
    _FLAT_CACHE = flat
    return _FLAT_CACHE


# --------------------------------------------------------------------------- #
#  Extraction PDF (PyMuPDF) — à la demande, cache mémoire, plafonnée.
# --------------------------------------------------------------------------- #
_PDF_TEXT_CACHE: dict[str, str] = {}
_PDF_MAX_CHARS = 24000


def _url_to_path(ctx: ToolContext, url: str) -> str:
    """Convertit une URL /data/scenarios/... en chemin disque data_dir."""
    if url.startswith("/data/scenarios/"):
        rel = url[len("/data/scenarios/"):]
    elif url.startswith("/data/"):
        rel = url[len("/data/"):]
    else:
        rel = url.lstrip("/")
    return os.path.join(ctx.data_dir, "scenarios", rel)


def extraire_pdf(ctx: ToolContext, pdf_url: str) -> str:
    """Extrait le texte d'un PDF à partir de son URL /data/ (cache + plafond)."""
    cle = pdf_url
    if cle in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[cle]
    path = _url_to_path(ctx, pdf_url)
    texte = ""
    try:
        import pymupdf  # pylint: disable=import-outside-toplevel
        with pymupdf.open(path) as doc:
            for page in doc:
                texte += page.get_text() + "\n\n---\n\n"
                if len(texte) >= _PDF_MAX_CHARS:
                    texte += "\n⚠️ (extrait tronqué — PDF complet consultable via l'URL)"
                    break
    except ImportError:
        texte = "(extraction PDF indisponible : pymupdf non installé)"
    except Exception as e:                                   # noqa: BLE001
        texte = f"(extraction impossible : {e})"
    _PDF_TEXT_CACHE[cle] = texte[:_PDF_MAX_CHARS + 120]
    return _PDF_TEXT_CACHE[cle]


# --------------------------------------------------------------------------- #
#  Enrichissement du bestiaire avec les monstres du scénario
# --------------------------------------------------------------------------- #
def _noms_monstres_scenario(s: dict[str, Any]) -> list[str]:
    """Collecte les noms de monstres cités par un scénario.

    Source fiable : la catégorie `artwork.monstres` du catalogue (le dossier
    artwork classe explicitement ces images comme « Monstres »). On ignore les
    doublons/cas vides. Les vrais stat-blocks des PDF ne sont pas fiables à
    parser automatiquement — on ne fait ici que garantir la PRÉSENCE d'une
    fiche pour chacun, qu'il sera possible d'enrichir ensuite.
    """
    noms: list[str] = []
    vus: set[str] = set()
    for m in (s.get("artwork") or {}).get("monstres") or []:
        nom = str((m or {}).get("nom") or "").strip()
        if not nom:
            continue
        cle = nom.lower().strip()
        if cle in vus:
            continue
        vus.add(cle)
        noms.append(nom)
    return noms


# --------------------------------------------------------------------------- #
#  Bible de scénario — construit une fiche structurée à partir du PDF pour
#  que le MJ puisse suivre la trame (et la difficulté) d'un scénario, même
#  après que l'historique de chat ait été tronqué.
# --------------------------------------------------------------------------- #
# Marqueurs (« signatures ») repérant l'édition du scénario dans le texte du
# PDF. Utile pour prévenir les incohérences (ex. un scénario Adventurers
# League 5e lancé dans une partie 3.5).
_SIGNATURES_5E = [
    "adventurers league", "organized play", "basic rules",
    "passive wisdom", "downtime", "background", "5th level",
    "4th level characters", "1st level character", "logsheet",
    "spellcasting services", "raise dead", "adventurers",
    # Modules français/custom marquent souvent l'édition explicitement.
    "d&d 5", "d&d5", "d&d 5e", "rules of d&d 5", "règles de d&d 5",
]
_SIGNATURES_35 = [
    "srd 3.5", "dungeon master's guide", "tome of horros",
    "3rd edition", "v3.5", "3.5", "d&d 3.5", "d&d3.5",
]

_ED5_RE = re.compile(r"\b(?:5e|dnd5|d&d\s?5e?)\b", re.IGNORECASE)
_ED35_RE = re.compile(r"\b3\.5\b")


def _detecter_edition(texte: str) -> str:
    """Détecte l'édition probable d'un scénario à partir de son texte PDF.
    Renvoie '5e', '3.5' ou 'inconnue' (signatures + marqueurs autonomes)."""
    bas = (texte or "")
    score5 = sum(1 for s in _SIGNATURES_5E if s in bas.lower())
    score3 = sum(1 for s in _SIGNATURES_35 if s in bas.lower())
    # Marqueurs autonomes « 5e » et « 3.5 » (robustes, non noyés dans les
    # signatures). On compte les occurrences uniques.
    score5 += len(_ED5_RE.findall(bas))
    score3 += len(_ED35_RE.findall(bas))
    if score5 > score3:
        return "5e"
    if score3 > score5:
        return "3.5"
    return "inconnue"


_NIV_PATTERN = re.compile(
    r"(\d+)\s*(?:st|nd|rd|th)?\s*(?:[-–—]\s*(\d+)\s*(?:st|nd|rd|th)?|to\s*(\d+))"
    r"\s*(?:level|niveau|level characters)|"
    r"(\d+)\s*(?:st|nd|rd|th)?\s*(?:level|niveau|level characters)",
    re.IGNORECASE,
)


def _extraire_niveau(texte: str) -> str:
    """Extrait la fourchette de niveaux recommandée (« 1st-4th level …
    characters ») depuis le texte du scénario. Heuristique : préfère une
    fourchette (1-4) sinon le niveau simple (1)."""
    m = _NIV_PATTERN.search(texte or "")
    if not m:
        return ""
    a, b, c = m.group(1), m.group(2), m.group(3)
    if b:
        return f"{a}-{b}"
    if c:
        return f"{a}-{c}"
    return a or str(m.group(4) or "")


def _resume_texte(texte: str, cible: int = 2200) -> str:
    """Condensé du début du scénario (synopsis/background/hook) pour la
    bible — suffisant au MJ sans noyer le contexte (~2 ko)."""
    if not texte:
        return ""
    # On privilégie les sections les plus utiles du livret si présentes.
    propre = texte.replace("\r", "")
    for section in ("Overview", "Adventure Background", "Adventure Hook",
                    "Introduction", "Background"):
        idx = propre.lower().find(section.lower())
        if idx != -1:
            debut = propre[idx: idx + cible]
            if len(debut) >= 200:
                return debut
    return propre[:cible] + ("…" if len(propre) > cible else "")


def _construire_bible(
    s: dict[str, Any], pdf_texte: str, edition_partie: str
) -> dict[str, Any]:
    """Construit la bible structurée d'un scénario (persistée dans
    `quete.bible` puis injectée au MJ à chaque tour)."""
    detectee = _detecter_edition(pdf_texte)
    niveau = str(s.get("niveau") or "") or _extraire_niveau(pdf_texte)
    bible: dict[str, Any] = {
        "titre": str(s.get("titre") or ""),
        "univers": str(s.get("_univers") or ""),
        "source_pdf": str(s.get("pdf") or ""),
        "edition_detectee": detectee,
        "edition_partie": edition_partie,
        "niveau_recommande": niveau,
        "joueurs_recommandes": str(s.get("joueurs") or ""),
        "resume": _resume_texte(pdf_texte),
        "etapes": [],              # étapes du scénario (voir scenario_etape)
        "etape_courante": "",
        "objectifs": [],           # objectifs/enjeux principaux
    }
    # Avertissement d'édition : un scénario clairement d'une AUTRE édition
    # que la partie risque de produire des monstres/difficultés incohérents.
    det = detectee.lower()
    partie_ok = "3.5" in edition_partie.lower()
    if det == "5e" and partie_ok:
        bible["avertissement"] = (
            "⚠️ Ce scénario est un module Adventurers League 5e : ses "
            "monstres, DD et niveaux d'XP sont calibrés 5e. Réinterprète "
            "les créatures avec les stats D&D 3.5 du bestiaire et ajuste "
            "la difficulté au niveau réel du groupe (ne copie pas les CR "
            "5e tels quels)."
        )
    else:
        bible["avertissement"] = ""
    return bible


# --------------------------------------------------------------------------- #
def _assurer_monstres_au_bestiaire(ctx: ToolContext, noms: list[str]) -> list[str]:
    """Garantit qu'UNE fiche bestiaire existe pour chaque monstre de scénario.

    Pour chaque nom absent du bestiaire, génère une fiche de secours
    (stats D&D 3.5 de base marquées « générique ») et la persiste dans
    `data/bestiaire.json`. Ainsi `engager_combat` (validation stricte) ne
    bloque plus jamais le combat contre un monstre du scénario — le MJ peut
    ensuite remplacer la fiche par les stats officielles.

    Renvoie la liste des noms AJOUTÉS.
    """
    if not noms:
        return []
    try:
        from .monstres import _find_monstre, _generer_monstre_genérique
    except Exception:                                        # noqa: BLE001
        return []
    ajoutes: list[str] = []
    for nom in noms:
        try:
            if _find_monstre(ctx, nom) is not None:
                continue  # déjà présent ou résoluble (alias humain inclus)
            if _generer_monstre_genérique(nom, ctx) is not None:
                ajoutes.append(nom)
        except Exception:                                    # noqa: BLE001
            continue
    return ajoutes


# --------------------------------------------------------------------------- #
#  Tools LLM
# --------------------------------------------------------------------------- #
@tool
async def scenarios_laelith_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste les scénarios disponibles, groupés par univers.
    Utiliser ensuite `scenarios_laelith_charger` pour récupérer un scénario.
    Aucun argument.
    """
    cata = charger_catalogue(ctx)
    universes = cata.get("universes", [])
    if not universes:
        return ToolResult(text="⚠️ Aucun scénario disponible.")
    lignes = ["📚 **Catalogue de scénarios par univers**"]
    for u in universes:
        noms_cartes = ""
        if u.get("cartes"):
            noms_cartes = f" ({len(u['cartes'])} cartes)"
        lignes.append(f"\n## {u.get('nom', '?')}{noms_cartes}")
        if u.get("description"):
            lignes.append(f"_{u['description']}_")
        for s in u.get("scenarios", []):
            pdf = " 📄" if s.get("pdf") else ""
            assets = []
            if s.get("cartes"): assets.append(f"{len(s['cartes'])} cartes")
            if s.get("artwork"): assets.append("artwork")
            if s.get("objets"): assets.append(f"{len(s['objets'])} objets")
            if s.get("enigmes"): assets.append(f"{len(s['enigmes'])} énigmes")
            if s.get("annexes"): assets.append(f"{len(s['annexes'])} annexes")
            extra = f" [{', '.join(assets)}]" if assets else ""
            lignes.append(f"- **[{s.get('id','?')}] {s.get('titre','?')}**{pdf}{extra}")
            if s.get("pitch"):
                lignes.append(f"  _{s['pitch']}_")
    lignes.append(
        "\n— Identifiant du scénario : `[id]`. "
        "Le texte PDF est extrait au chargement. Les images/artwork/objets "
        "sont consultables via les URLs du catalogue."
    )
    return ToolResult(text="\n".join(lignes))


@tool
async def scenarios_laelith_charger(
    ctx: ToolContext, scenario_id: str
) -> ToolResult:
    """
    Charge le détail d'un scénario par son identifiant. Pour un scénario PDF,
    le texte intégral est extrait du fichier (plafonné). L'URL publique du
    PDF est incluse pour que les joueurs le consultent.
    Le MJ adapte ensuite le pitch via etat_partie_patch (quete.titre / pitch).

    :param scenario_id (str): identifiant du scénario tel que listé.
    """
    flat = _charger_catalogue_plat(ctx)
    sid = str(scenario_id).strip()
    s = next((x for x in flat if str(x.get("id", "")) == sid), None)
    if s is None:
        return ToolResult(
            text=(
                f"❌ Scénario '{scenario_id}' introuvable. "
                f"Utilisez `scenarios_laelith_lister` pour voir les IDs."
            )
        )
    champs = [
        f"📜 **Scénario {s.get('id','?')} : {s.get('titre','?')}**",
        f"- Univers : {s.get('_univers', '?')}",
    ]
    if s.get("pitch"):
        champs.append(f"- Pitch : {s['pitch']}")
    if s.get("pdf"):
        champs.append(f"- 📄 PDF consultable par les joueurs : {s['pdf']}")
        # Extraire texte pour le MJ
        texte = extraire_pdf(ctx, s["pdf"])
        champs.append(f"\n=== TEXTE DU SCÉNARIO (extrait) ===\n{texte}")
    else:
        texte = ""
    # ── Bible du scénario : fiche structurée (édition, niveaux, résumé)      ──
    # Persistée dans `quete.bible`, réinjectée au MJ à chaque tour — il garde
    # ainsi la trame du scénario même quand l'historique de chat est tronqué.
    edition_partie = "D&D 3.5"
    try:
        if ctx.partie_id:
            from ..game.state import PartyState
            _st0 = PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id)
            _et0 = _st0.load()
            edition_partie = str(_et0.get("meta", {}).get("regles") or "D&D 3.5")
    except Exception:                                            # noqa: BLE001
        pass
    bible = _construire_bible(s, texte, edition_partie)
    champs.append(
        "\n### 📖 Bible du scénario (réinjectée au MJ à chaque tour)\n"
        f"- Édition détectée : {bible['edition_detectee']} "
        f"(partie : {edition_partie})"
        + (f"\n- Niveaux recommandés : {bible['niveau_recommande']}"
           if bible["niveau_recommande"] else "")
        + (f"\n- Nombre de joueurs : {bible['joueurs_recommandes']}"
           if bible["joueurs_recommandes"] else "")
    )
    if bible.get("avertissement"):
        champs.append(f"\n{bible['avertissement']}")
    # Assets
    for label, cle in [("Cartes", "cartes"), ("Objets", "objets"),
                       ("Énigmes", "enigmes")]:
        items = s.get(cle)
        if items:
            champs.append(f"\n### {label} ({len(items)})")
            for item in items:
                champs.append(f"- {item.get('nom', '?')} : {item.get('fichier', '?')}")
    if s.get("artwork"):
        art = s["artwork"]
        for cat_label, cat_key in [("Lieux", "lieux"), ("Monstres", "monstres"), ("PNJ", "pnj")]:
            imgs = art.get(cat_key)
            if imgs:
                champs.append(f"\n### Artwork — {cat_label} ({len(imgs)})")
                for img in imgs:
                    champs.append(f"- {img.get('nom', '?')} : {img.get('fichier', '?')}")
    if s.get("annexes"):
        champs.append(f"\n### Annexes ({len(s['annexes'])})")
        for a in s["annexes"]:
            champs.append(f"- {a.get('nom', '?')} : {a.get('fichier', '?')}")
    # Auto-patch quête (titre + pitch + source + Bible — conservée en l'état
    # à travers `quete.bible`, y compris les étapes/objectifs suivis).
    quete = {
        "titre": str(s.get("titre", "")),
        "pitch": str(s.get("pitch", "")),
        "source": f"[{s.get('id','')}] " + str(s.get("pdf") or s.get("_univers", "")),
        "bible": bible,
    }
    # ── Bestiaire : garantit une fiche pour chaque monstre du scénario ──
    ajoutes = _assurer_monstres_au_bestiaire(ctx, _noms_monstres_scenario(s))
    if ajoutes:
        champs.append(
            "\n### Bestiaire — monstres du scénario ajoutés (fiches de secours génériques)\n"
            + ", ".join(ajoutes)
        )

    # ── Mémoire de campagne : quête/mission + position (+ résumé à remplir) ──
    try:
        if ctx.partie_id:
            from ..game.state import PartyState
            st = PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id)
            etat = st.load()
            mem = etat.setdefault("memoire", {})
            mem.setdefault("missions", [])
            # Mission active (la quête du scénario devient la mission courante)
            titre_mission = str(quete.get("titre") or "").strip()
            if titre_mission and not any(
                str(x.get("titre", "")).lower() == titre_mission.lower()
                for x in mem["missions"]
            ):
                mem["missions"].append({
                    "titre": titre_mission,
                    "statut": "active",
                    "notes": str(quete.get("pitch") or ""),
                    "ts": _now_iso(),
                })
            # Position : l'univers/le scénario donne un point d'ancrage
            unit = str(s.get("_univers") or "")
            mem.setdefault("position", {"lieu": "", "zone": "", "detail": ""})
            if unit and not mem["position"].get("lieu"):
                mem["position"]["lieu"] = unit
            mem.setdefault("objectif_courant", str(quete.get("pitch") or ""))
            # Persiste la quête avec sa bible (étapes/objectifs suivis) — que
            # la phase suivante retrouve la trame même sans l'historique.
            etat["quete"] = {
                "titre": str(s.get("titre", "")),
                "pitch": str(s.get("pitch", "")),
                "source": quete.get("source", ""),
                "bible": quete.get("bible", {}),
            }
            st.save(etat)
    except Exception:                                           # noqa: BLE001
        pass

    champs.append(
        "\nℹ️ Dans `memoire_resume`, mets à jour `memoire_intrigue` "
        "(résumé + objectif) pour garder le fil de l'histoire."
    )
    return ToolResult(text="\n".join(champs), state_patch={"quete": quete})


@tool
async def scenario_etape(
    ctx: ToolContext,
    etape: str = "",
    avancement: str = "",
    objectif: str = "",
    terminée: bool = False,
) -> ToolResult:
    """
    Suit l'avancement du scénario en cours : enregistre l'étape ACTUELLE de
    la trame et (optionnellement) une étape nouvellement ACCOMPLIE. À appeler
    dès qu'un objectif du scénario est atteint ou que le groupe change
    d'étape. Ces informations sont réinjectées au MJ à chaque tour : c'est ce
    qui garantit qu'on reste sur la trame du scénario malgré l'improvisation
    et la troncature de l'historique.

    :param etape (str): l'étape de la trame que le groupe est EN TRAIN de
        vivre (ex. « explorer les catacombes pour trouver l'origine des
        morts-vivants »).
    :param avancement (str): état/notes courtes sur l'avancement (optionnel).
    :param objectif (str): l'objectif immédiat du groupe (optionnel).
    :param terminée (bool): si `True`, enregistre `etape` comme étape
        accomplie (déplacée dans la liste des étapes réalisées).
    """
    from ..game.state import PartyState
    st = PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id)
    etat = st.load()
    if "_erreur" in etat:
        return ToolResult(text="❌ État de partie illisible.")
    quete = etat.setdefault("quete", {})
    bible = quete.setdefault("bible", {})
    bible.setdefault("etapes", [])
    bible.setdefault("etapes_terminees", [])
    etape = str(etape or "").strip()
    if not etape:
        return ToolResult(text="❌ Décris l'étape courante.")
    if bool(terminée):
        if etape and str(etape).lower() not in [
            str(x).lower() for x in (bible.get("etapes_terminees") or [])
        ]:
            bible["etapes_terminees"] = (bible.get("etapes_terminees") or []) + [etape][-40:]
        bible["etape_courante"] = ""
    else:
        bible["etape_courante"] = etape
    if str(avancement or "").strip():
        bible["avancement"] = str(avancement).strip()[:600]
    if str(objectif or "").strip():
        bible["objectif"] = str(objectif).strip()[:600]
    elif not bool(terminée):
        bible["objectif"] = etape[:600]
    st.save(etat)
    texte = (
        f"📌 Étape scénario : « {etape} »"
        + (" — accomplie ✅" if bool(terminée) else " — en cours")
    )
    if (bible.get("etapes_terminees")):
        texte += "\nÉtapes accomplies : " + ", ".join(
            bible["etapes_terminees"][-6:])
    return ToolResult(text=texte, state_patch={"quete": quete})
