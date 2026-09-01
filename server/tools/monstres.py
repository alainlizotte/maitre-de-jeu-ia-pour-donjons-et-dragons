"""Outil Monstres — adapté de `Outil_ImageMonstre.py`.

Renvoie la fiche complète + génère/c sert l'image d'un monstre D&D 3.5.

Spécificité de l'app standalone (vs OpenWebUI) :
- Pas de backend de génération d'image AI (pas d'OpenWebUI AutoImage ici).
  À la place : un **placeholder SVG stylé** est généré et mis en cache sous
  `data/bestiaire_cache/<slug>.svg`. Si une vraie image PNG existe déjà au
  même chemin, elle est servie en priorité. L'utilisateur peut déposer ses
  propres PNGs dans ce dossier pour remplacer les placeholders.
- L'image est exposée au front via l'URL `/data/bestiaire_cache/<slug>.svg`
  (route servie par StaticFiles dans `main.py`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool
from ..image.helpers import generer_averti, monstre_prompt


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
_BESTIAIRE_CACHE: Optional[dict[str, Any]] = None
_BESTIAIRE_MTIME: float = 0.0


def _slug(s: str) -> str:
    nf = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:60].strip("_").lower() or "monstre"


# Marqueurs de numérotation ajoutés aux homonymes en combat (« Gobelin »,
# « Gobelin (2) », « Gobelin 2 », « Gobelin #2 »). On les retire pour obtenir
# le NOM DE TYPE canonique : deux créatures identiques doivent partager la
# MÊME image, pas en générer une aléatoire par individu numéroté.
_TYPE_SUFFIX_RE = re.compile(
    r"(?:[\s_\-]*[\(\[](?:#?\d+)[\)\]])|(?:[\s_\-]+#?\d+)$",
    re.IGNORECASE,
)


def _type_nom(nom: str) -> str:
    """Nom de type canonique d'un monstre de combat.

    « Gobelin (2) » → « Gobelin », « Gobelin #3 » → « Gobelin ». Sert de clé
    de cache d'image pour qu'un groupe de monstres identiques réutilise la
    même illustration au lieu d'en régénérer une aléatoire par individu.
    """
    n = str(nom or "").strip()
    for _ in range(3):
        dec = _TYPE_SUFFIX_RE.sub("", n).strip()
        if dec == n:
            break
        n = dec
    return n.strip() or str(nom or "").strip()


def _cache_key(nom: str) -> str:
    """Clé de cache d'image d'un monstre (slug du nom de type canonique).

    Centralise tous les chemins image sur le NOM DE TYPE : « Gobelin (2) »
    partage le PNG/méta de « Gobelin ».
    """
    return _slug(_type_nom(nom))


def _bestiaire_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "bestiaire.json")


def _load_bestiaire(ctx: ToolContext) -> dict[str, Any]:
    """Charge le bestiaire avec cache memo recharge si mtime change."""
    global _BESTIAIRE_CACHE, _BESTIAIRE_MTIME
    path = _bestiaire_path(ctx)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {"_meta": {"nb_monstres": 0}, "monstres": {}}
    if _BESTIAIRE_CACHE is None or mt != _BESTIAIRE_MTIME:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Le bestiaire source a les monstres en clés top-level (hors _meta).
            monstres: dict[str, Any] = {}
            for k, v in raw.items():
                if k == "_meta":
                    continue
                if isinstance(v, dict) and "nom" in v:
                    monstres[v.get("cle", k)] = v
            raw["monstres"] = monstres
            _BESTIAIRE_CACHE = raw
            _BESTIAIRE_MTIME = mt
        except (json.JSONDecodeError, OSError):
            return {"_meta": {"nb_monstres": 0}, "monstres": {}}
    return _BESTIAIRE_CACHE  # type: ignore[return-value]


# Prompts « génériques » écrits par les scripts d'import (enrichir_bestiaire,
# import_bestiaire_drs) : ils ne contiennent que le nom, aucune info visuelle.
_GENERIC_PROMPT_RE = re.compile(
    r"^fantasy\s+.+\bcreature\b,\s*D&D 3\.5( manual)? illustration",
    re.IGNORECASE,
)

# Traduction FR→EN des types du bestiaire — le générateur d'images (Qwen-Image)
# réagit bien mieux aux mots-clés anglais (« undead », « giant »…).
_TYPES_EN: list[tuple[str, str]] = [
    ("mort-vivant", "undead creature, rotting gray flesh"),
    ("extérieur", "outsider"),
    ("exterieur", "outsider"),
    ("élémentaire", "elemental"),
    ("elementaire", "elemental"),
    ("créature magique", "magical beast"),
    ("creature magique", "magical beast"),
    ("créature monstrueuse", "monstrous beast"),
    ("créature artificielle", "construct"),
    ("créature feérique", "fey creature"),
    ("créature aberrante", "aberration"),
    ("monstre aberrant", "aberration"),
    ("humanoïde", "humanoid"),
    ("humanoide", "humanoid"),
    ("aberration", "aberration"),
    ("animal", "animal"),
    ("bête", "beast"),
    ("dragon", "dragon"),
    ("fée", "fey"),
    ("géant", "giant"),
    ("construct", "construct"),
    ("vase", "ooze"),
    ("ver", "vermin creature"),
    ("plante", "plant creature"),
]


def _type_en(type_fr: str) -> str:
    """Traduit un type de monstre FR (« Mort-vivant ») en mot-clé EN."""
    t = type_fr.strip().lower()
    for fr, en in _TYPES_EN:
        if t.startswith(fr):
            return en
    return ""


def _desc_locale(m: Optional[dict[str, Any]]) -> str:
    """Description visuelle d'un monstre depuis le bestiaire local.

    - `prompt_image` riche (ex. goule : « ghoul undead creature, gaunt
      human, elongated claws... ») → utilisé tel quel ;
    - sinon on retombe sur le type traduit en anglais (donne déjà
      « undead creature » à Qwen-Image au lieu de rien du tout).
    Renvoie "" si le monstre est absent ou sans information exploitable.
    """
    if m is None:
        return ""
    pi = str(m.get("prompt_image") or "").strip()
    if pi and not _GENERIC_PROMPT_RE.match(pi):
        return pi
    return _type_en(str(m.get("type", "")))


async def _desc_via_rag(nom: str) -> str:
    """Cherche la description physique du monstre dans la KB RAG.

    Le Manuel des Monstres 3.5 (ingéré dans le vector store) commence chaque
    entrée par un portrait : « Ce mort-vivant ressemble à un humain émacié,
    à la chair grise et décomposée... ». C'est la meilleure source quand le
    bestiaire local n'a pas de prompt visuel exploitable. Fail-safe : toute
    erreur (embedding server down, chroma absente...) renvoie "".
    """
    try:
        from ..config import get_config
        from ..rag.store import get_store

        store = get_store(get_config())
        hits = await store.query(
            f"{nom} description physique apparence aspect du monstre",
            top_k=8,
        )
    except Exception:                                        # noqa: BLE001
        return ""
    n = _normalise_nom(nom)
    morceaux: list[str] = []
    total = 0
    for h in hits:
        # On ne garde que les extraits qui parlent bien DE ce monstre.
        hay = _normalise_nom(h.title + " " + h.text[:300])
        if n not in hay and not any(p in hay for p in n.split("_") if len(p) >= 4):
            continue
        extrait = h.text.strip()
        if total + len(extrait) > 900:
            extrait = extrait[: max(0, 900 - total)]
        if not extrait:
            break
        morceaux.append(extrait)
        total += len(extrait)
        if total >= 900:
            break
    return "\n".join(morceaux)


def _cache_dir(ctx: ToolContext) -> str:
    path = os.path.join(ctx.data_dir, "bestiaire_cache")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _normalise_nom(nom: str) -> str:
    """Normalise un nom pour recherche insensible à la casse/accents."""
    nf = unicodedata.normalize("NFKD", nom.lower())
    return re.sub(r"[^a-z0-9]+", "_", "".join(c for c in nf if not unicodedata.combining(c)))


# Le MJ (LLM) nomme souvent les monstres en anglais (« Ghoul », « Goblin »…)
# alors que le bestiaire local est en français (« Goule », « Gobelin »).
# Sans ce pont, la fiche est introuvable ET le prompt d'image part vide →
# le générateur invente un monstre quelconque.
_ALIAS_EN_FR: dict[str, str] = {
    "ghoul": "goule",
    "ghast": "goule",
    "skeleton": "squelette",
    "zombie": "zombie",
    "goblin": "gobelin",
    "hobgoblin": "hobgobelin",
    "bugbear": "gobelours",
    "orc": "orque",
    "ogre": "ogre",
    "ogre_mage": "ogre-mage",
    "troll": "troll",
    "gnoll": "gnoll",
    "minotaur": "minotaure",
    "gargoyle": "gargouille",
    "basilisk": "basilic",
    "chimera": "chimere",
    "cockatrice": "cocatrix",
    "djinni": "djinn",
    "efreeti": "efrit",
    "ettin": "ettin",
    "griffin": "griffon",
    "griffon": "griffon",
    "harpy": "harpie",
    "hippogriff": "hippogriffe",
    "manticore": "manticore",
    "medusa": "meduse",
    "mimic": "mimique",
    "mummy": "momie",
    "unicorn": "licorne",
    "vampire": "vampire",
    "wyvern": "wyverne",
    "succubus": "succube",
    "pixie": "pixie",
    "nymph": "nymphe",
    "pegasus": "pegase",
    "satyr": "satyre",
    "centaur": "centaure",
    "werewolf": "loup_garou",
    "wolf": "loup",
    "dire_wolf": "loup_terrible",
    "worg": "worg",
    "owlbear": "ours_hibou",
    "shadow": "ombre",
    "wraith": "spectre",
    "spectre": "spectre",
    "green_hag": "guenaude_verte",
    "sea_hag": "guenaude_marine",
    "purple_worm": "ver_pourpre",
    "flesh_golem": "golem_de_chair",
    "clay_golem": "golem_d_argile",
    "iron_golem": "golem_de_fer",
    "stone_golem": "golem_de_pierre",
    "kraken": "kraken",
    "lamia": "lamie",
    "rakshasa": "rakshasa",
    "tarasque": "tarasque",
    "tarrasque": "tarasque",
    "hydra": "hydre_5_tetes",
    "sahuagin": "sahuagin",
    "locathah": "locathah",
    "troglodyte": "troglodyte",
    "ettercap": "ettercap",
    "otyugh": "otyugh",
    "remorhaz": "remorhaz",
}

# Traduction mot à mot (secours pour les noms composés non listés ci-dessus :
# « young_red_dragon » → « jeune_rouge_dragon » ≈ inclusion dans
# `dragon_rouge_jeune` grâce à la recherche par inclusion).
_MOTS_EN_FR: dict[str, str] = {
    "red": "rouge", "black": "noir", "blue": "bleu", "green": "vert",
    "white": "blanc", "brass": "laiton", "bronze": "bronze",
    "copper": "cuivre", "gold": "or", "silver": "argent",
    "young": "jeune", "adult": "adulte", "old": "vieux",
    "ancient": "ancien", "elder": "ancien",
    "giant": "geant", "dire": "terrible", "great": "grand",
    "hill": "collines", "frost": "givre", "fire": "feu",
    "cloud": "nuages", "stone": "pierres", "storm": "tempetes",
    "bear": "ours", "spider": "araignee", "bat": "chauve_souris",
    "rat": "rat", "scorpion": "scorpion", "crocodile": "crocodile",
    "octopus": "pieuvre", "squid": "calmar", "snake": "serpent",
    "constrictor": "constricteur", "bee": "abeille", "wasp": "guepe",
    "beetle": "scarabee", "ant": "fourmi", "eagle": "aigle",
    "lion": "lion", "tiger": "tigre", "hyena": "hyene",
    "dragon": "dragon", "dragonne": "dragonne",
}

# Adversaires humains / PNJ « inventés » par un scénario → fiche officielle du
# bestiaire. Le MJ ne doit JAMAIS inventer les stats d'un garde, bandit ou
# chenapan de la foule : on les ramène à leur entrée canonique dans le
# bestiaire (gardes, chenaille, bandits, hommes d'armes). Clés = variantes
# génériques FR (normalisées), valeurs = clé canonique du bestiaire.
_ALIAS_HUMAIN_GENERIQUE: dict[str, str] = {
    # Gardes / soldats / surveillance
    "gardes": "garde", "garde": "garde", "garde_de_la_ville": "garde",
    "gardien_humain": "garde", "soldat_humain": "garde",
    "milice_humaine": "garde", "homme_d_armes": "garde",
    "hommes_d_armes": "garde", "guerrier_humain": "garde",
    "sergent_humain": "garde",
    # Foule / populace / chenapan
    "chenaille": "chenaille", "cheneaille": "chenaille",
    "chenapan": "chenaille", "chenapans": "chenaille",
    "chenapane": "chenaille", "chenapanes": "chenaille",
    "foule_humaine": "chenaille", "foule": "chenaille", "populace": "chenaille",
    "paysan_humain": "chenaille", "paysan": "chenaille",
    "paysans_humains": "chenaille", "paysans": "chenaille",
    "paysanne": "chenaille", "paysannes": "chenaille",
    "gredin_humain": "chenaille", "gredins_humains": "chenaille",
    "gredin": "chenaille", "gredins": "chenaille",
    "ruffian_humain": "chenaille", "ruffians_humains": "chenaille",
    "ruffian": "chenaille", "ruffians": "chenaille",
    "voyou_humain": "chenaille", "voyous_humains": "chenaille",
    "voyou": "chenaille", "voyous": "chenaille",
    "malfrat_humain": "chenaille", "malfrats_humains": "chenaille",
    "malfrat": "chenaille", "malfrats": "chenaille",
    # Bandits / brigands
    "bandit": "bandit", "bandits": "bandit", "brigand_humain": "bandit",
    "brigand": "bandit", "brigands_humains": "bandit", "brigands": "bandit",
    "pillard_humain": "bandit", "pillard": "bandit", "pillards_humains": "bandit",
    "pillards": "bandit", "maraudeur_humain": "bandit",
    "maraudeur": "bandit", "maraudeurs_humains": "bandit",
    "maraudeurs": "bandit", "voleur_humain": "bandit",
    "voleur": "bandit", "voleurs_humains": "bandit", "voleurs": "bandit",
    "assassin_humain": "bandit", "assassin": "bandit", "assassins": "bandit",
    # Hommes d'armes aasimar / gardes d'élite
    "aasimar": "aasimar_homme_d_armes_de_niveau_1",
    "aasimar_homme_d_armes": "aasimar_homme_d_armes_de_niveau_1",
    "homme_d_armes_aasimar": "aasimar_homme_d_armes_de_niveau_1",
    "chevalier_aasimar": "aasimar_homme_d_armes_de_niveau_1",
    "paladin_aasimar": "aasimar_homme_d_armes_de_niveau_1",
}


def _candidats_noms(nom: str) -> list[str]:
    """Noms candidats pour la recherche : original + alias EN→FR + traduction
    mot à mot (dédupe en conservant l'ordre)."""
    n = _normalise_nom(nom)
    cands = [nom, n]
    alias = _ALIAS_EN_FR.get(n)
    if alias:
        cands.append(alias)
        cands.append(_normalise_nom(alias))
    # Alias « PNJ humains génériques » → fiche officielle du bestiaire
    # (garde, chenaille, bandit, aasimar…). On ajoute la clique canonique pour
    # que garde_de_la_ville → garde_humain_guerrier_2, etc. On balaie aussi
    # chaque mot significatif du nom (« une meute de chenapans » → chenaille).
    generique = _ALIAS_HUMAIN_GENERIQUE.get(n)
    if generique:
        cands.append(generique)
        cands.append(_normalise_nom(generique))
    else:
        for w in n.split("_"):
            if len(w) < 3:
                continue
            g = _ALIAS_HUMAIN_GENERIQUE.get(w)
            if g:
                cands.append(g)
                cands.append(_normalise_nom(g))
    trad = "_".join(_MOTS_EN_FR.get(w, w) for w in n.split("_"))
    if trad != n:
        cands.append(trad)
    return list(dict.fromkeys(cands))


def _find_monstre(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Cherche un monstre par nom FR ou EN (insensible casse/accents/alias,
    y compris noms composés réordonnés : « Young Red Dragon » →
    « dragon_rouge_jeune » via sous-ensemble de mots traduits)."""
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})
    cands = _candidats_noms(nom)
    # 1. clé exacte (slugifiée) — sur chaque candidat (alias inclus)
    for cand in cands:
        n = _normalise_nom(cand)
        for k, m in monstres.items():
            if _normalise_nom(k) == n or _normalise_nom(m.get("nom", "")) == n:
                return m
    # 2. inclusion (« dragon rouge » → « dragon_rouge_jeune »)
    for cand in cands:
        n = _normalise_nom(cand)
        if len(n) < 4:
            continue
        for k, m in monstres.items():
            if n in _normalise_nom(k) or n in _normalise_nom(m.get("nom", "")):
                return m
    # 3. sous-ensemble de mots traduits — gère les ordres différents
    #    ({jeune,rouge,dragon} ⊆ {dragon,rouge,jeune}) ; la clé la plus
    #    longue gagne.
    meilleur: Optional[tuple[int, dict[str, Any]]] = None
    for cand in cands:
        mots = [
            w for w in _normalise_nom(cand).split("_")
            if len(w) >= 3 and w != "monstre"
        ]
        if not mots:
            continue
        for k, m in monstres.items():
            nk = _normalise_nom(k)
            if all(w in nk for w in mots):
                if meilleur is None or len(nk) > meilleur[0]:
                    meilleur = (len(nk), m)
    if meilleur:
        return meilleur[1]
    # 4. mot-clé partagé : UN mot significatif de la requête correspond à un
    #    MOT ENTIER de la clé (« squelette armé d'une hache » → « squelette »,
    #    « goule des cryptes » → « goule »). Meilleur recouvrement d'abord,
    #    clé la plus longue pour départager. Singulier/pluriel naïf.
    def _sing(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 3 else w

    meilleur4: Optional[tuple[int, int, dict[str, Any]]] = None
    for k, m in monstres.items():
        nk_tokens = {_sing(w) for w in _normalise_nom(k).split("_")}
        if not nk_tokens:
            continue
        score = 0
        for cand in cands:
            qwords = [w for w in _normalise_nom(cand).split("_") if len(w) >= 4]
            score += sum(1 for w in qwords if _sing(w) in nk_tokens)
        # on ne garde que les recouvrements réels (au moins un mot entier)
        if score == 0:
            continue
        # à recouvrement égal : la clé la plus COURTE gagne (nom canonique
        # simple « Squelette » plutôt que « … hommes d'armes de niveau 1 »)
        if meilleur4 is None or (score, -len(k)) > (meilleur4[0], -meilleur4[1]):
            meilleur4 = (score, len(k), m)
    return meilleur4[2] if meilleur4 else None


def _find_monstre_with_fallback(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Cherche un monstre dans le bestiaire. Si aucun match exact, tente un
    monstre générique basé sur le type et la taille demandés."""
    m = _find_monstre(ctx, nom)
    if m is not None:
        return m
    # Tenter un monstre générique (ex. "Créature magique de taille G")
    generique = _generer_monstre_genérique(nom, ctx)
    return generique


def _suggestions(monstres: dict[str, Any], cands: list[str],
                 limite: int = 3) -> list[str]:
    """Noms du bestiaire les plus proches d'une requête non résolue
    (recouvrement de mots entiers, puis longueur)."""
    def _sing(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 3 else w

    scores: list[tuple[int, int, str]] = []
    for k in monstres:
        nk_tokens = {_sing(w) for w in _normalise_nom(k).split("_")}
        score = 0
        for cand in cands:
            qwords = [w for w in _normalise_nom(cand).split("_")
                      if len(w) >= 4]
            score += sum(1 for w in qwords if _sing(w) in nk_tokens)
        if score:
            scores.append((-score, len(k), k))
    scores.sort()
    return [k for _, _, k in scores[:limite]]


# Mapping tailles FR → facteur de mise à l'échelle des PV / CA
_TAILLE_ECHELLE: dict[str, dict[str, Any]] = {
    # taille → {pv_factor, ca_mod, label}
    "T":  {"pv_factor": 0.5, "ca_mod": -1, "label": "petite"},
    "P":  {"pv_factor": 0.75, "ca_mod": 0, "label": "petite"},
    "M":  {"pv_factor": 1.0, "ca_mod": 0, "label": "moyenne"},
    "G":  {"pv_factor": 1.5, "ca_mod": 1, "label": "grande"},
    "TG": {"pv_factor": 2.0, "ca_mod": 2, "label": "très grande"},
    "Gig":{"pv_factor": 3.0, "ca_mod": 3, "label": "gigantesque"},
    "Col":{"pv_factor": 4.0, "ca_mod": 4, "label": "colossale"},
}

# Alias de tailles (texte libre → clé)
_TAILLE_ALIAS: dict[str, str] = {}
for _k, _v in _TAILLE_ECHELLE.items():
    _TAILLE_ALIAS[_k.lower()] = _k
    _TAILLE_ALIAS[_v["label"].lower()] = _k
_taille_extra = {
    "t": "T", "p": "P", "m": "M", "g": "G", "tg": "TG",
    "gigantesque": "Gig", "colossal": "Col", "colossale": "Col",
    "tiny": "T", "small": "P", "medium": "M", "large": "G",
    "huge": "TG", "gargantuan": "Gig", "colossal": "Col",
    "minuscule": "T", "très petite": "P", "très grande": "TG",
}
_TAILLE_ALIAS.update(_taille_extra)


def _extraire_taille(nom: str) -> Optional[str]:
    """Extrait la taille d'un nom de monstre (ex. 'Créature magique de taille G' → 'G')."""
    n = nom.lower()
    # Cherche "taille X" ou "size X"
    m = re.search(r"(?:taille|size)\s+([A-Za-z]+)", n)
    if m:
        t = _TAILLE_ALIAS.get(m.group(1).lower())
        if t:
            return t
    # Cherche la taille seule en fin de chaîne
    for alias, cle in sorted(_TAILLE_ALIAS.items(), key=lambda x: -len(x[0])):
        if n.endswith(alias):
            return cle
    return None


def _generer_monstre_genérique(
    nom: str, ctx: ToolContext
) -> Optional[dict[str, Any]]:
    """Génère un monstre générique basé sur le nom et la taille demandés.

    Quand aucun monstre du bestiaire ne correspond, on crée une fiche
    minimaliste avec des stats de base proportionnées à la taille demandée.
    On tente aussi de copier la description visuelle (prompt_image) d'un
    monstre du même type dans le bestiaire pour que l'image générée
    ressemble à quelque chose de cohérent.
    Renvoie None si même le type ne peut pas être déterminé.
    """
    taille_cle = _extraire_taille(nom) or "M"
    echelle = _TAILLE_ECHELLE.get(taille_cle, _TAILLE_ECHELLE["M"])

    # Extraire le type de créature du nom
    type_fr = ""
    n_lower = nom.lower()
    for type_key, _ in _TYPES_EN:
        if type_key in n_lower:
            type_fr = type_key
            break

    # PV de base selon la taille (fourchette D&D 3.5 standard)
    pv_base = {
        "T": 3, "P": 6, "M": 10, "G": 20, "TG": 35, "Gig": 60, "Col": 100,
    }
    pv = max(1, int(pv_base.get(taille_cle, 10) * echelle["pv_factor"]))
    ca = 10 + echelle["ca_mod"]

    # Chercher un monstre du même type dans le bestiaire pour copier sa description
    prompt_description = ""
    if type_fr:
        best = _load_bestiaire(ctx)
        monstres_dict = best.get("monstres", {})
        for m in monstres_dict.values():
            if m.get("type", "").lower().startswith(type_fr.lower()):
                pi = str(m.get("prompt_image") or "").strip()
                if pi and not _GENERIC_PROMPT_RE.match(pi):
                    prompt_description = pi
                    break

    fiche = {
        "nom": nom,
        "type": type_fr or "inconnu",
        "taille": taille_cle,
        "dv": "1d8",
        "pv": pv,
        "pv_max": pv,
        "ca": ca,
        "vitesse": "9m",
        "bab": "+0",
        "init": "+0",
        "attaques": "1 attaque corpo",
        "degs": "1d6",
        "sauvegardes": "Vig +0, Réf +0, Vol +0",
        "carac": "For 10, Dex 10, Con 10, Int 2, Sag 10, Cha 10",
        "comp": "",
        "dons": "",
        "capacites": "",
        "faiblesses": "",
        "fp": "1/4",
        "alignement": "Neutre",
        "cle": _slug(nom),
        "prompt_image": prompt_description or (
            f"fantasy {type_fr or 'creature'} creature, "
            f"D&D style illustration, ink style, dramatic lighting"
        ),
        "generique": True,
    }

    # Persister dans le bestiaire pour les prochains appels
    try:
        best_path = _bestiaire_path(ctx)
        try:
            with open(best_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = {}
        cle = _slug(nom)
        raw[cle] = fiche
        if "_meta" in raw and isinstance(raw["_meta"], dict):
            raw["_meta"]["nb_monstres"] = sum(
                1 for k in raw if k != "_meta"
                and isinstance(raw[k], dict) and "nom" in raw[k]
            )
        with open(best_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        global _BESTIAIRE_CACHE
        _BESTIAIRE_CACHE = None
    except Exception:
        pass

    return fiche


def _find_image(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie le chemin d'une vraie image (PNG/JPG/WebP) en cache.

    On exclut le `.svg` car c'est par convention un placeholder affiché en
    dépannage — pas une « vraie » image. On préfère générer un vrai PNG
    via ComfyUI plutôt que resservir un vieux SVG. Le cache est indexé sur le
    nom de TYPE (`_cache_key`) pour que les homonymes partagent une image.
    """
    slug = _cache_key(nom)
    cache_dir = _cache_dir(ctx)
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = os.path.join(cache_dir, f"{slug}.{ext}")
        if os.path.isfile(p):
            return p
    return None


def _find_svg(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie le SVG placeholder existant (le cas échéant)."""
    slug = _cache_key(nom)
    p = os.path.join(_cache_dir(ctx), f"{slug}.svg")
    return p if os.path.isfile(p) else None


def _placeholder_svg(nom: str) -> str:
    """Génère un SVG placeholder stylé D&D pour un monstre."""
    initiales = "".join(w[0].upper() for w in re.findall(r"[A-Za-zÀ-ÿ]+", nom)[:2]) or "?"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        f'width="256" height="256" role="img" aria-label="{nom}">'
        f'<rect width="256" height="256" fill="#1a1a23" />'
        f'<rect x="4" y="4" width="248" height="248" fill="none" '
        f'stroke="#8a6d3b" stroke-width="2" rx="10" ry="10" />'
        f'<text x="128" y="135" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="100" font-weight="bold" '
        f'fill="#c4a96a">{initiales}</text>'
        f'<text x="128" y="200" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="20" fill="#b0b0b5">'
        f'<tspan>{nom}</tspan></text>'
        f'<text x="128" y="230" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="11" '
        f'font-style="italic" fill="#777">D&D 3.5 — placeholder</text>'
        f'</svg>'
    )


def _write_placeholder(ctx: ToolContext, nom: str) -> str:
    """Écrit le placeholder SVG et renvoie son chemin."""
    path = os.path.join(_cache_dir(ctx), f"{_cache_key(nom)}.svg")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_placeholder_svg(nom))
    except OSError:
        pass
    return path


def _url_for(path: str, data_dir: str) -> str:
    """Retourne l'URL publique pour servir l'image (StaticFiles sur `/data`).

    `path` est un chemin absolu sous `data_dir` ; on le rend relatif à `data_dir`
    et on préfixe par `/data/` (mount StaticFiles ajouté dans `main.py`).
    """
    from pathlib import Path
    try:
        rel = Path(path).relative_to(Path(data_dir))
        return "/data/" + rel.as_posix().lstrip("/")
    except ValueError:
        # En cas de chemin hors data_dir, on retombe sur le basename seul.
        return "/data/" + Path(path).name


def _format_fiche(m: dict[str, Any]) -> str:
    """Formate la fiche d'un monstre en Markdown lisible."""
    lignes = [
        f"🐉 **{m.get('nom','?')}** — FP {m.get('fp','?')}",
        f"- Type : {m.get('type','?')} ( taille {m.get('taille','?')} )",
        f"- DV : {m.get('dv','?')} — PV : {m.get('pv','?')} — CA : {m.get('ca','?')}",
        f"- Vitesse : {m.get('vitesse','?')} — Initiative : {m.get('init','?')}",
        f"- Attaques : {m.get('attaques','?')}",
        f"- Dégâts : {m.get('degs','?')}",
        f"- Sauvegardes : {m.get('sauvegardes','?')}",
        f"- Carac : {m.get('carac','?')}",
        f"- Compétences : {m.get('comp','?')}",
        f"- Dons : {m.get('dons','?')}",
        f"- Capacités : {m.get('capacites','—')}",
        f"- Faiblesses : {m.get('faiblesses','—')}",
        f"- Alignement : {m.get('alignement','?')}",
    ]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
#  Description + invalidation du cache d'images
# --------------------------------------------------------------------------- #
async def _description_monstre(m: Optional[dict[str, Any]], nom: str) -> str:
    """Meilleure description visuelle disponible : prompt du bestiaire local,
    sinon portrait du Manuel des Monstres via la KB RAG (requête avec le nom
    canonique FR — les manuels ingérés sont en VF)."""
    description = _desc_locale(m)
    if description:
        return description
    return await _desc_via_rag(str((m or {}).get("nom") or nom))


def _hash_desc(description: str) -> str:
    """Hash court de la description (invalidation du cache d'images).

    Inclut la version du prompt template (PROMPT_VERSION) pour forcer la
    régénération de toutes les images quand le prompt change (ex. ajout
    d'anti-texte renforcé).
    """
    try:
        from ..image.helpers import PROMPT_VERSION
        version = PROMPT_VERSION
    except ImportError:
        version = "v1"
    combined = f"{version}|{description}"
    return (
        hashlib.sha1(combined.encode("utf-8")).hexdigest()[:12]
        if description else ""
    )


def _meta_path_for(ctx: ToolContext, slug: str) -> str:
    return os.path.join(_cache_dir(ctx), f"{slug}.meta.json")


def _read_desc_hash(meta_path: str) -> Optional[str]:
    """Lit le hash de la description stocké (`desc_hash_v2`).

    La clé en v2 invalide tous les métadonnées anciennes (v1) : les images
    générées avec l'ancien template de prompt — souvent ornées d'écritures —
    sont ainsi régénérées automatiquement.
    """
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f).get("desc_hash_v2")
    except (OSError, ValueError):
        return None


def _write_desc_hash(meta_path: str, h: str, nom: str) -> None:
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"desc_hash_v2": h, "nom": nom}, f, ensure_ascii=False)
    except OSError:
        pass


async def image_pour(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie l'URL de l'illustration du monstre `nom`, en la générant au
    besoin via ComfyUI (cache prioritaire ; régénération si la fiche du
    bestiaire a été enrichie ou si le PNG date de l'ancien template).

    Utilisé par le hook post-tour de `main.py` : quand un monstre entre en
    jeu sans que le MJ ait appelé `monstre_consulter`, la table voit quand
    même son portrait. Renvoie toujours une URL (placeholder SVG en dernier
    recours), None seulement si même le placeholder est impossible.
    """
    m = _find_monstre(ctx, nom)
    slug = _cache_key(nom)
    dest = os.path.join(_cache_dir(ctx), f"{slug}.png")
    meta_path = _meta_path_for(ctx, slug)
    img_path = _find_image(ctx, nom)
    description = ""
    if img_path is not None:
        meta_hash = _read_desc_hash(meta_path)
        hash_local = _hash_desc(_desc_locale(m))
        if meta_hash is not None and meta_hash == hash_local and hash_local:
            return _url_for(img_path, ctx.data_dir)
        description = await _description_monstre(m, nom)
        if meta_hash is not None and _hash_desc(description) == meta_hash:
            return _url_for(img_path, ctx.data_dir)
        # Image périmée → on supprime le fichier AVANT l'appel, sinon
        # generer_averti le voit déjà présent et court-circuite (cache hit)
        # sans rien régénérer.
        try:
            os.remove(img_path)
        except OSError:
            pass
    else:
        description = await _description_monstre(m, nom)
    nom_canonique = str((m or {}).get("nom") or _type_nom(nom))
    prompt_text = monstre_prompt(_type_nom(nom_canonique), description)
    try:
        r = await generer_averti(ctx, "monstre", prompt_text, dest)
    except Exception:
        r = None
    if r is not None and os.path.isfile(r):
        _write_desc_hash(meta_path, _hash_desc(description), nom_canonique)
        return _url_for(r, ctx.data_dir)
    img_path = _find_image(ctx, nom) or _write_placeholder(ctx, nom)
    return _url_for(img_path, ctx.data_dir)


def _norm_nom_simple(s: Any) -> str:
    """Comparaison de noms insensible casse/accents (journal ↔ combat)."""
    s = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _fusionner_rencontres(
    etat: dict, rencontres: list[tuple[str, str]]
) -> bool:
    """Fusionne des (nom, url) dans le journal `rencontres_images` de l'état.

    Mute `etat` en place (pas de save ici — l'appelant persiste). Renvoie
    True si le journal a changé. Déduplique par nom normalisé, borne à 30.
    """
    connus = {
        _norm_nom_simple(r.get("nom"))
        for r in etat.get("rencontres_images") or []
    }
    changed = False
    for nom, url in rencontres:
        if not nom or not url:
            continue
        key = _norm_nom_simple(nom)
        if key in connus:
            continue
        etat.setdefault("rencontres_images", []).append(
            {"nom": str(nom), "url": url}
        )
        connus.add(key)
        changed = True
    if len(etat.get("rencontres_images") or []) > 30:
        etat["rencontres_images"] = etat["rencontres_images"][-30:]
        changed = True
    return changed


def _memoriser_rencontre(ctx: ToolContext, nom: str, url: str) -> None:
    """Persiste l'illustration d'une rencontre dans l'état de partie.

    - `monstres_combat[i].image_url` : si le monstre est engagé au combat,
      son portrait survit aux rechargements de page (le front le réaffiche
      jusqu'à sa mort) ;
    - `rencontres_images` : journal des monstres croisés en jeu (exploration
      comprise) pour réhydrater la galerie « Monstres rencontrés ».

    Fail-safe : toute erreur est silencieusement ignorée.
    """
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        st = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = st.load()
        if "_erreur" in etat:
            return
        changed = False
        for m in etat.get("monstres_combat") or []:
            if (
                _norm_nom_simple(m.get("nom")) == _norm_nom_simple(nom)
                and m.get("image_url") != url
            ):
                m["image_url"] = url
                changed = True
        if _fusionner_rencontres(etat, [(nom, url)]):
            changed = True
        if changed:
            st.save(etat)
    except Exception:                                            # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def monstre_consulter(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Renvoie la fiche complète (statistiques D&D 3.5) + l'URL d'une image d'un
    monstre. Cherche dans le bestiaire local (`data/bestiaire.json`) en
    acceptant les noms français OU anglais (alias : « Ghoul » → « Goule »).
    Si le monstre n'est pas trouvé localement, retourne un message invitant à
    interroger la base de connaissances RAG « D&D 3.5 — Manuels » et
    génère quand même un placeholder SVG. L'image est régénérée
    automatiquement si la fiche du bestiaire a été enrichie depuis.

    :param nom (str): nom du monstre, FR ou EN (ex. "Gobelin"/"Goblin",
        "Goule"/"Ghoul", "Dragon rouge jeune").
    """
    m = _find_monstre(ctx, nom)
    # Image : priorité PNG en cache à jour, sinon génération ComfyUI, sinon
    # placeholder SVG. « À jour » = le hash de la description stocké dans
    # <slug>.meta.json correspond à la description courante — un vieux PNG
    # généré sans description (monstre inventé par l'IA) est régénéré dès
    # qu'une vraie fiche est disponible. Le cache est indexé sur le nom de
    # TYPE (`_cache_key`) pour que les homonymes partagent une image.
    slug = _cache_key(nom)
    cache_dir = _cache_dir(ctx)
    dest = os.path.join(cache_dir, f"{slug}.png")
    meta_path = _meta_path_for(ctx, slug)

    img_path = _find_image(ctx, nom)
    src = "locale"
    description = ""
    if img_path is not None:
        meta_hash = _read_desc_hash(meta_path)
        # 1er test rapide avec la seule description locale (évite un appel
        # RAG systématique quand le bestiaire suffit).
        hash_local = _hash_desc(_desc_locale(m))
        if meta_hash is not None and meta_hash == hash_local and hash_local:
            src = "cache"
        else:
            # Description complète (locale ou portrait RAG du Manuel des
            # Monstres, requête avec le nom canonique FR).
            description = await _description_monstre(m, nom)
            if meta_hash is not None and _hash_desc(description) == meta_hash:
                src = "cache"
            else:
                # Image périmée → suppression AVANT régénération, sinon
                # generer_averti court-circuite sur le fichier existant.
                try:
                    os.remove(img_path)
                except OSError:
                    pass
                img_path = None  # image périmée → régénération
    if img_path is None:
        gen_ok = False
        try:
            nom_canonique = str((m or {}).get("nom") or nom)
            if not description:
                description = await _description_monstre(m, nom)
            prompt_text = monstre_prompt(nom_canonique, description)
            r = await generer_averti(ctx, "monstre", prompt_text, dest)
            if r is not None and os.path.isfile(r):
                img_path = r
                src = "comfyui"
                gen_ok = True
                _write_desc_hash(meta_path, _hash_desc(description), nom_canonique)
        except Exception as e:
            # On ne casse pas le tour si ComfyUI échoue — fallback SVG.
            src = f"comfyui_echec({type(e).__name__})"
        if not gen_ok:
            if img_path is None:
                img_path = _find_image(ctx, nom)
            if img_path is None:
                img_path = _write_placeholder(ctx, nom)
                if "comfyui" not in src:
                    src = "placeholder"
    url = _url_for(img_path, ctx.data_dir)
    # Persiste la rencontre (journal + combat éventuel) pour que le portrait
    # survive aux rechargements de page, jusqu'à la mort du monstre.
    _memoriser_rencontre(
        ctx, str((m or {}).get("nom") or nom), url
    )
    if m is None:
        # Suggestions de noms proches dans le bestiaire (fail-safe : jamais
        # bloquant si le bestiaire est illisible).
        try:
            sugg = _suggestions(
                _load_bestiaire(ctx).get("monstres", {}),
                _candidats_noms(nom),
            )
        except Exception:                                        # noqa: BLE001
            sugg = []
        texte = (
            f"❓ Monstre **{nom}** absent du bestiaire local. "
            f"Pour les stats, interrogez la KB « D&D 3.5 — Manuels » "
            f"(RAG activé). "
            f"Image ({src}) : {url}"
        )
        if sugg:
            texte += f"\n🔎 Noms proches dans le bestiaire : {', '.join(sugg)}."
        texte += (
            "\n⚔️ Ce monstre engage le groupe ? Appelle "
            "`calculer_initiative` puis `demarrer_combat` AVANT toute "
            "attaque ou action de combat."
        )
        return ToolResult(text=texte, state_patch={"image_monstre": url})
    fiche = _format_fiche(m)
    return ToolResult(
        text=(
            f"{fiche}\n\n🖼️ Image ({src}) : {url}\n"
            f"\n[JSON complet]\n" + json.dumps(m, ensure_ascii=False, indent=2)
            + "\n⚔️ Ce monstre engage le groupe ? Appelle "
              "`calculer_initiative` puis `demarrer_combat` AVANT toute "
              "attaque ou action de combat."
        ),
        state_patch={"image_monstre": url},
    )


@tool
async def monstre_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste tous les monstres pré-remplis du bestiaire local, avec leur FP.
    Aucun argument. Utile pour que le MJ choisisse enemis crédibles sans
    improviser les stats.
    """
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})
    if not monstres:
        return ToolResult(text="ℹ️ Bestiaire local vide.")
    lignes = []
    # Tri par FP (parsing approximatif : 1/3 < 1 < 2 ...)
    def fp_key(m: dict[str, Any]) -> float:
        fp = str(m.get("fp", "0"))
        try:
            if "/" in fp:
                a, b = fp.split("/")
                return float(a) / float(b)
            return float(fp)
        except (ValueError, ZeroDivisionError):
            return 99.0
    for m in sorted(monstres.values(), key=fp_key):
        lignes.append(f"- **{m.get('nom','?')}** — FP {m.get('fp','?')}")
    return ToolResult(
        text=(
            f"🐉 Bestiaire local ({len(monstres)} monstres, triés par FP) :\n"
            + "\n".join(lignes)
        )
    )


@tool
async def monstre_ajouter_bestiaire(
    ctx: ToolContext,
    nom: str,
    type_monstre: str,
    taille: str,
    dv: str,
    pv: int,
    ca: int,
    vitesse: str,
    bab: str,
    init: str,
    attaques: str,
    degs: str,
    sauvegardes: str,
    carac: str,
    comp: str,
    dons: str,
    capacites: str,
    faiblesses: str,
    fp: str,
    alignement: str,
) -> ToolResult:
    """
    Enrichit le bestiaire local en y ajoutant un monstre custom (pour les
    créatures non couvertes par le Manuel des Monstres 3.5 d'origine). La
    fiche est persistée dans `data/bestiaire.json` et réutilisable à
    l'avenir.

    :param nom (str): nom usuel (ex. "Rois des glaces").
    :param type_monstre (str): ex. "EI (froid)".
    :param taille (str): T/P/M/G/C (cf. MJ 3.5).
    :param dv (str): ex. "4d8+8".
    :param pv (int): points de vie moyens.
    :param ca (int): classe d'armure.
    :param vitesse (str): ex. "9 m (6 cases)".
    :param bab (str): ex. "+4".
    :param init (str): ex. "+1".
    :param attaques (str): description des armes/modes d'attaque.
    :param degs (str): dégâts par attaque.
    :param sauvegardes (str): "Réfl +X, Vig +Y, Vol +Z".
    :param carac (str): "For X, Dex Y, Con Z, Int A, Sag B, Cha C".
    :param comp (str): compétences.
    :param dons (str): dons.
    :param capacites (str): capacités spéciales.
    :param faiblesses (str): faiblesses (— si aucune).
    :param fp (str): facteur de puissance (ex. "1/2", "3").
    :param alignement (str): ex. "Neutre mauvais".
    """
    best_path = _bestiaire_path(ctx)
    try:
        with open(best_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ToolResult(text=f"❌ Bestiaire illisible : {best_path}")

    cle = _slug(nom)
    fiche = {
        "nom": nom,
        "type": type_monstre,
        "taille": taille,
        "dv": dv,
        "pv": int(pv),
        "ca": int(ca),
        "vitesse": vitesse,
        "bab": bab,
        "init": init,
        "attaques": attaques,
        "degs": degs,
        "sauvegardes": sauvegardes,
        "carac": carac,
        "comp": comp,
        "dons": dons,
        "capacites": capacites,
        "faiblesses": faiblesses,
        "fp": fp,
        "alignement": alignement,
        "cle": cle,
        "prompt_image": f"fantasy {_type_en(type_monstre) or type_monstre.lower()} creature, "
                        f"D&D style illustration, ink style, dramatic lighting",
    }
    raw[cle] = fiche
    # Met à jour le meta nb_monstres
    if "_meta" in raw and isinstance(raw["_meta"], dict):
        raw["_meta"]["nb_monstres"] = sum(1 for k in raw if k != "_meta" and isinstance(raw[k], dict) and "nom" in raw[k])

    try:
        with open(best_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur écriture bestiaire : {e}")
    # Invalide le cache
    global _BESTIAIRE_CACHE
    _BESTIAIRE_CACHE = None
    return ToolResult(
        text=f"✅ Monstre **{nom}** ajouté au bestiaire (clé `{cle}`, FP {fp}).",
    )
