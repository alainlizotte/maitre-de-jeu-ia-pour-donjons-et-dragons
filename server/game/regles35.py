"""Règles 3.5 partagées — défenses des monstres, dangers d'environnement,
trésors par ND. Nouveau module « chantier DRS » (audit rules coverage).

Tout ce qui est ici est CÂBLÉ : les moteurs (combat, sorts, tools) appellent
ces helpers ; le MJ n'a plus à deviner les valeurs.

Sections :
  1. Défenses des monstres (RD, résistances énergie, immunités, vulnérabilités)
  2. Dangers d'environnement (chute, noyade/asphyxie, chaleur, froid, fumée,
     acide)
  3. Trésors aléatoires par ND (tables DMG 3.5 — page « Trésors » DRS)

Imports paresseux partout : aucun cycle avec tools.*.
"""

from __future__ import annotations

import random
import re
import unicodedata
from typing import Any, Optional

# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _norm(s: Any) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def _norm_txt(s: str) -> str:
    return str(s or "").replace("–", "-").replace("—", "-").replace("*", "")


# --------------------------------------------------------------------------- #
#  1. DÉFENSES DES MONSTRES
# --------------------------------------------------------------------------- #
# Éléments d'énergie reconnaissables dans une source de dégâts (arme, sort).
_ELEMENTS = {
    "feu": ("feu", "flamme", "incendie", "embraser", "boule de feu",
            "brul", "braises", "flame"),
    "froid": ("froid", "glace", "givre", "glacial", "hypothermie",
              "morsure glac"),
    "electricite": ("electricite", "foudre", "eclair", "electr", "choc"),
    "acide": ("acide", "corrosif", "corrosion"),
    "sonique": ("sonique", "son", "stridulation", "cri"),
}

# Matériaux / types qui CONTOURNENT une RD, détectés dans le nom de la source
# (« épée longue +1 », « dague en argent », « marteau en adamantium »…).
_BYPASS_MOTS = {
    "magie": ("+1", "+2", "+3", "+4", "+5", "magique", "enchant"),
    "argent": ("argent",),
    "fer froid": ("fer froid",),
    "adamantium": ("adamantium",),
    "bien": ("bien",),          # RD X/Bien — non exploitable côté armes
    "mal": ("mal",),
}

# --------------------------------------------------------------------------- #
#  Type de dégâts des armes (PHB 3.5, Table 7-5) — chantier « RD par type »
# --------------------------------------------------------------------------- #
# contondant / perforant / tranchant par nom d'arme normalisé (« Mains nues »
# = contondant, PHB ; « Filet » n'inflige pas de dégâts typés).
_TYPE_DEGATS_ARME: dict[str, str] = {
    # contondant
    "baton": "contondant", "matraque": "contondant",
    "masse d armes legere": "contondant", "masse d armes lourde": "contondant",
    "etoile du matin": "contondant", "fleau d armes": "contondant",
    "fleau d armes leger": "contondant", "fleau d armes lourd": "contondant",
    "fleau double": "contondant", "marteau de guerre": "contondant",
    "marteau leger": "contondant", "gantelet": "contondant",
    "gantelet cloute": "contondant", "gourdin": "contondant",
    "massue": "contondant", "nunchaku": "contondant", "bolas": "contondant",
    "fronde": "contondant", "lance d arcon": "contondant",
    "mains nues": "contondant", "coup": "contondant", "poing": "contondant",
    "tentacule": "contondant", "queue": "contondant",
    # perforant
    "dague": "perforant", "dague coup-de-poing": "perforant",
    "lance courte": "perforant", "lance": "perforant",
    "lance longue": "perforant", "javeline": "perforant",
    "javelot": "perforant", "flechette": "perforant", "sarbacane": "perforant",
    "arbalete legere": "perforant", "arbalete lourde": "perforant",
    "arbalete de poing": "perforant",
    "arbalete legere a repetition": "perforant",
    "arbalete lourde a repetition": "perforant", "epee courte": "perforant",
    "rapiere": "perforant", "trident": "perforant", "arc court": "perforant",
    "arc long": "perforant", "arc court composite": "perforant",
    "arc long composite": "perforant", "epieu": "perforant",
    "pique": "perforant", "marteau-piolet gnome": "perforant",
    "pic de guerre leger": "perforant", "pic de guerre lourd": "perforant",
    "sai": "perforant", "siangham": "perforant", "shuriken": "perforant",
    "corseque": "perforant", "fleche": "perforant", "carreau": "perforant",
    "morsure": "perforant", "corne": "perforant", "dard": "perforant",
    # tranchant
    "faucille": "tranchant", "epee longue": "tranchant",
    "epee batarde": "tranchant", "hache d arme": "tranchant",
    "hachette": "tranchant", "hache a deux mains": "tranchant",
    "espadon": "tranchant", "glaive": "tranchant", "hallebarde": "tranchant",
    "guisarme": "tranchant", "ransueur": "tranchant", "faux": "tranchant",
    "cimeterre": "tranchant", "cimeterre a deux mains": "tranchant",
    "coutille": "tranchant", "grande hache": "tranchant", "kama": "tranchant",
    "fouet": "tranchant", "hache de guerre naine": "tranchant",
    "chaine cloutee": "tranchant", "double-lame": "tranchant",
    "hache double orque": "tranchant", "urgrosh nain": "tranchant",
    "kukri": "tranchant", "griffe": "tranchant", "serpe": "tranchant",
    "lame": "tranchant", "hache": "tranchant", "epee": "tranchant",
    "sabre": "tranchant",
}

# Mots-clés de secours (armes naturelles du bestiaire hors catalogue).
_MOTS_TYPE_DEGATS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contondant", ("contondant", "massue", "marteau", "masse", "gourdin",
                    "fleau", "baton", "matraque", "mains nues", "poing",
                    "coup ", "nunchaku", "bolas", "fronde")),
    ("perforant", ("perforant", "dague", "lance", "javel", "pique", "pieu",
                   "rapiere", "arbalete", "arc ", "fleche", "carreau",
                   "morsure", "corne", "dard", "sai ", "siangham",
                   "shuriken", "epieu", "sarbacane", "flechette")),
    ("tranchant", ("tranchant", "epee", "hache", "lame", "sabre", "cimeterre",
                   "faux", "griffe", "glaive", "hallebarde", "guisarme",
                   "faucille", "kama", "fouet", "chaine cloutee", "urgrosh",
                   "kukri", "coutille")),
)


def type_degats_source(source: str) -> str:
    """Type de dégâts d'une attaque d'arme : « tranchant », « perforant » ou
    « contondant » (PHB Table 7-5), '' si indéterminé (sort, effet…).

    1) nom d'arme du CATALOGUE cité dans la source (« Épée longue +1 »
    contient « épée longue ») ; 2) mots-clés (armes naturelles du bestiaire :
    morsure → perforant, griffe → tranchant, coup → contondant)."""
    s = _norm(source)
    if not s:
        return ""
    from ..catalogue import ARMES   # lazy : évite les cycles
    for arme in ARMES:
        nom_norm = _norm(str(arme.get("nom") or ""))
        if nom_norm and nom_norm in s:
            t = _TYPE_DEGATS_ARME.get(nom_norm)
            if t:
                return t
    for t, mots in _MOTS_TYPE_DEGATS:
        for mot in mots:
            if mot in s:
                return t
    return ""

_RE_RD = re.compile(
    r"reduction\s+des\s+degats\s*\(?([^)]*)\)?", re.IGNORECASE)
_RE_RD2 = re.compile(r"\bRD\s*[: ]?\s*(\d+)\s*(?:/([\w\s/]+))?", re.IGNORECASE)
_RE_RESIST = re.compile(
    r"résistance\s+(?:à|au|aux|contre)?\s*(?:la\s+magie|l'acide|au\s+feu"
    r"|au\s+froid|à\s+l'électricité|à\s+la\s+foudre|au\s+son)"
    r"(?:\s+et\s+(?:au|à|l')\s*\w+)?\s*\(?(\d+)\)?", re.IGNORECASE)
_RE_RESIST2 = re.compile(r"resistance\s+\w+[^.)]{0,40}?\((\d+)\)", re.IGNORECASE)


def defenses_monstre(m: dict) -> dict:
    """Parse les défenses d'une fiche bestiaire → dict exploitable.

    Sources : champs `capacites` et `faiblesses` du bestiaire (texte DRS) :
      - « réduction des dégâts (10/fer froid ou Bien) » / « RD 5/magie »
      - « résistance à l'acide, au feu et au froid (10) »
      - « immunité contre l'électricité » / « immunité contre le feu »
      - « vulnérabilité au feu » / « fer froid inflige le double de dégâts »
    Retour : {"rd": [(10, "fer froid"), ...], "resist": {"feu": 10, …},
              "immune": {"feu", …}, "vuln": {"feu", …}}
    """
    texte = _norm(_norm_txt(str(m.get("capacites") or "")) + " ;; "
                  + _norm_txt(str(m.get("faiblesses") or "")))
    if not texte.strip():
        return {}
    out: dict[str, Any] = {"rd": [], "resist": {}, "immune": set(),
                           "vuln": set()}
    # --- RD -----------------------------------------------------------------
    for bloc in _RE_RD.findall(texte):
        for morceau in re.split(r"[,;]", bloc):
            mrd = re.search(r"(\d+)\s*(?:/([\w\s/]+))?", morceau)
            if not mrd:
                continue
            bypass = (_norm(mrd.group(2) or "—").replace(" ou ", "/")
                      .strip("/ ") or "—")
            out["rd"].append((int(mrd.group(1)), bypass))
    for mrd in _RE_RD2.finditer(texte):
        bypass = _norm(mrd.group(2) or "—").strip("/ ") or "—"
        out["rd"].append((int(mrd.group(1)), bypass))
    # --- Résistances énergie --------------------------------------------------
    # « résistance au feu et au froid (10) » / « résistance à l'acide (10) » :
    # on capte la LISTE d'éléments entre « resistance » et « (N) ».
    for bloc in re.finditer(
            r"resistance\s+(?P<liste>(?:[a-z']+\s+){0,4}?)"
            r"[^.)]{0,60}?\((\d+)\)",
            texte, re.IGNORECASE):
        liste = _norm(bloc.group("liste") or "") + " " + str(bloc.group(0))
        n = int(bloc.group(2))
        elems = set()
        for m_elem in re.finditer(
                r"(acide|feu|froid|electricite|foudre|son|magie)",
                liste):
            elems.add(_elem_canonique(m_elem.group(1)))
        for elem in elems:
            if elem and elem not in out["resist"]:
                out["resist"][elem] = n
    # --- Immunités / vulnérabilités ------------------------------------------
    for m_imm in re.finditer(
            r"immunite contre (?:l'|le |la |les )?([a-z'\s]{3,20})", texte):
        elem = _elem_canonique(_norm(m_imm.group(1)))
        if elem:
            out["immune"].add(elem)
    for m_vul in re.finditer(
            r"vulnerabilite (?:au|a la|a l')([a-z'\s]{3,15})", texte):
        elem = _elem_canonique(_norm(m_vul.group(1)))
        if elem:
            out["vuln"].add(elem)
    if "fer froid inflige le double" in texte:
        out["vuln"].add("fer_froid_x2")
    # Rien trouvé → dict vide (test de vérité fiable pour les appelants).
    if not (out["rd"] or out["resist"] or out["immune"] or out["vuln"]):
        return {}
    return out


def _elem_canonique(mot: str) -> str:
    m = _norm(mot)
    for elem, mots in _ELEMENTS.items():
        if any(k in m for k in mots):
            return elem
    if "electric" in m or "foudre" in m or "eclair" in m:
        return "electricite"
    return ""


def element_de_source(source: str) -> str:
    """Élément d'énergie d'une source de dégâts (« Boule de feu » → feu)."""
    s = _norm(source)
    for elem, mots in _ELEMENTS.items():
        if any(k in s for k in mots):
            return elem
    return ""


def _bypass_de_source(source: str, bonus_mag: int) -> set[str]:
    """Conditions de RD CONTOURNÉES par la source (« épée longue +1 » →
    magie) ET **type de dégâts de l'arme** (chantier « RD par type » :
    une masse contourne « RD 5/contondant », une épée non)."""
    s = _norm(_norm_txt(source))
    out: set[str] = set()
    if bonus_mag >= 1 or re.search(r"\+\d", s):
        out.add("magie")
    for bypass, mots in _BYPASS_MOTS.items():
        if bypass == "magie":
            continue
        if any(k in s for k in mots):
            out.add(bypass)
    type_d = type_degats_source(source)
    if type_d:
        out.add(type_d)
    return out


def est_une_attaque_darme(source: str) -> bool:
    """La source ressemble-t-elle à une ATTAQUE D'ARME (RD applicable) et
    non à un sort/effet ? (les sorts ne subissent pas la RD)."""
    from ..tools.dice import _est_nom_de_sort   # lazy : évite les cycles
    return not _est_nom_de_sort(source)


def reduire_degats_monstre(
    m: dict, degats: int, source: str = "", bonus_mag: int = 0,
) -> tuple[int, str]:
    """Applique RD / résistances / immunités / vulnérabilités d'un monstre à
    un montant de dégâts. Retour `(degats_reduits, note)` — note vide si rien
    n'a changé. `source` = nom d'arme ou de sort ; vide = type inconnu
    (la RD s'applique alors par prudence, la note le signale)."""
    d = defenses_monstre(m)
    if not d:
        return degats, ""
    nom_monstre = str(m.get("nom") or "Monstre")
    notes: list[str] = []
    total = int(degats)
    elem = element_de_source(source)
    # 1) Immunité d'élément ---------------------------------------------------
    if elem and elem in d["immune"]:
        notes.append(
            f"🛡️ **{nom_monstre} est IMMUNISÉ** contre {elem} — dégâts annulés."
        )
        return 0, " ".join(notes)
    # 2) Vulnérabilité (×1,5) -------------------------------------------------
    if elem and elem in d["vuln"]:
        total = int(total * 1.5)
        notes.append(
            f"🔥 **{nom_monstre} est VULNÉRABLE** à {elem} — dégâts ×1,5 "
            f"({degats} → {total}).")
    if "fer_froid_x2" in d["vuln"] and "fer froid" in _norm(source):
        total = total * 2
        notes.append(
            f"❄️ **{nom_monstre}** : le FER FROID inflige le double "
            f"({degats} → {total}).")
    # 3) Résistance énergie (−N) ----------------------------------------------
    if elem and elem in d["resist"]:
        avant = total
        total = max(0, total - int(d["resist"][elem]))
        if total != avant:
            notes.append(
                f"🛡️ Résistance {elem} {d['resist'][elem]} de "
                f"{nom_monstre} : {avant} → {total}.")
    # 4) Réduction des dégâts -------------------------------------------------
    if est_une_attaque_darme(source) and d["rd"]:
        bypass = _bypass_de_source(source, bonus_mag)
        for (montant, conditions) in d["rd"]:
            # Sémantique PHB 3.5 : « fer froid/bien » = ALTERNATIVES (un seul
            # terme suffit) ; « Bien et perforant » = CONJONCTION (il faut
            # TOUS les termes, ex. une arme à la fois bien et perforante).
            contourne = False
            for bloc in str(conditions).split("/"):
                sous = {_norm(c) for c in re.split(r"\s+ou\s+|\s+et\s+", bloc)
                        if c.strip() and c.strip() not in ("—", "-", "")}
                if sous and sous <= bypass:
                    contourne = True
                    break
            if contourne:
                continue                       # la source contourne cette RD
            avant = total
            total = max(0, total - montant)
            if total != avant:
                notes.append(
                    f"🛡️ RD {montant}/{conditions} de {nom_monstre} : "
                    f"{avant} → {total}.")
            break                              # une seule RD s'applique (3.5)
    return max(0, total), " ".join(notes)


# --------------------------------------------------------------------------- #
#  2. DANGERS D'ENVIRONNEMENT
# --------------------------------------------------------------------------- #
def degats_chute(
    hauteur_m: float, saut_delibre: bool = False,
    test_saut_reussi: bool = False, surface_meuble: bool = False,
) -> tuple[str, str]:
    """Dégâts de chute 3.5 : 1d6 par 3 m (max 20d6).

    Retour `(formule, explication)` — ex. ("2d6", "chute de 7 m").
    Réductions 3.5 : saut délibéré → les 1d6 premiers deviennent non-létaux ;
    test de Saut DD 15 réussi → 3 premiers mètres annulés, 3 suivants non-
    létaux ; surface meuble (sable, boue) → les 1d6 premiers non-létaux
    (cumul)."""
    tranches = min(20, max(0, int(float(hauteur_m or 0) // 3)))
    if tranches <= 0:
        return ("0", "chute de moins de 3 m — aucun dégât.")
    if test_saut_reussi:
        tranches = max(0, tranches - 1)
    formule = f"{tranches}d6"
    expl = f"chute de {hauteur_m:g} m → {tranches}d6"
    if test_saut_reussi:
        expl += " (test de Saut DD 15 réussi : 3 m annulés)"
    elif saut_delibre:
        expl += " (saut délibéré : les 1d6 premiers sont non-létaux)"
    if surface_meuble:
        expl += " (réception sur surface meuble : 1d6 non-létal)"
    return (formule, expl)


def regles_noyade(con: int) -> dict[str, Any]:
    """Table de noyade/asphyxie 3.5 : souffle = 2 × CON rounds, puis test de
    CON par round (DD 10, +1/round). Échec → round 1 : 0 PV inconscient,
    round 2 : -1 PV mourant, round 3 : mort."""
    souffle = max(1, int(con or 10) * 2)
    return {"souffle_rounds": souffle, "dd_premier": 10,
            "sequence": ["0 PV + Inconscient", "-1 PV + Mourant", "Mort"]}


def jets_chaleur(dd_base: int = 15, nb_heures: int = 8, armure: bool = False,
                 survie: int = 0, rng: Optional[random.Random] = None) -> dict:
    """Chaleur accablante (≥ 35° C) : jet de Vigueur PAR HEURE (DD 15, +1 par
    jet effectué au préalable), échec = 1d4 non-létaux. Armure épaisse :
    -4 ; Survie : bonus passé en `survie`. Retour dict avec total non-létal
    et détail des jets."""
    rng = rng or random
    jets: list[dict[str, Any]] = []
    non_letal = 0
    for h in range(1, min(24, max(1, nb_heures)) + 1):
        dd = dd_base + (h - 1)
        malus = -4 if armure else 0
        jet = rng.randint(1, 20)
        total = jet + malus + survie
        reussi = total >= dd or jet == 20
        if not reussi and jet != 1:
            reussi = False
        if not reussi:
            d4 = rng.randint(1, 4)
            non_letal += d4
        jets.append({"heure": h, "dd": dd, "jet": jet, "total": total,
                     "reussi": reussi,
                     "nl": 0 if reussi else rng.randint(1, 4)})
    non_letal = sum(j["nl"] for j in jets)
    return {"non_letal": non_letal, "jets": jets}


def jets_froid(dd_base: int = 15, nb_jets: int = 8, vetements_chauds: bool = False,
               survie: int = 0, rng: Optional[random.Random] = None) -> dict:
    """Froid (< 5° C) : jet de Vigueur par heure (DD 15, +1/précédent),
    échec = 1d6 non-létaux. Vêtements chauds : 1 jet/heure au lieu de
    1 jet/10 min (froid important : −15° C → 1 jet/10 min, 6 jets/h)."""
    rng = rng or random
    jets: list[dict[str, Any]] = []
    for h in range(1, min(24, max(1, nb_jets)) + 1):
        dd = dd_base + (h - 1)
        jet = rng.randint(1, 20)
        total = jet + survie
        reussi = total >= dd or jet == 20
        jets.append({"heure": h, "dd": dd, "jet": jet, "total": total,
                     "reussi": reussi,
                     "nl": 0 if reussi else rng.randint(1, 6)})
    return {"non_letal": sum(j["nl"] for j in jets), "jets": jets,
            "vetements_chauds": vetements_chauds}


# --------------------------------------------------------------------------- #
#  3. TRÉSORS ALÉATOIRES PAR ND (DMG 3.5 / DRS p. « Trésors »)
# --------------------------------------------------------------------------- #
# Tables condensées du DMG 3.5 : par ND, trois jets d100 (pièces, biens
# précieux, objets). Format : (min, max, formule) — formule vide = rien.
_TRESON_PIECES: dict[int, list[tuple[int, int, str]]] = {
    1: [(15, 29, "1d6x1000 pc"), (30, 52, "1d8x100 pa"), (53, 95, "2d8x10 po"),
        (96, 100, "1d3x10 pp")],
    2: [(14, 23, "1d10x1000 pc"), (24, 43, "2d10x100 pa"),
        (44, 95, "4d10x10 po"), (96, 100, "1d4x10 pp")],
    3: [(12, 21, "2d10x1000 pc"), (22, 41, "4d8x100 pa"),
        (42, 95, "1d4x100 po"), (96, 100, "1d6x10 pp")],
    4: [(12, 21, "3d10x1000 pc"), (22, 41, "4d12x1000 pa"),
        (42, 95, "1d6x100 po"), (96, 100, "1d8x10 pp")],
    5: [(11, 19, "1d4x10000 pc"), (20, 38, "1d6x1000 pa"),
        (39, 95, "1d8x100 po"), (96, 100, "1d10x10 pp")],
    6: [(11, 18, "1d6x10000 pc"), (19, 37, "1d8x1000 pa"),
        (38, 95, "1d10x100 po"), (96, 100, "1d12x10 pp")],
    7: [(12, 18, "1d10x10000 pc"), (19, 35, "1d12x1000 pa"),
        (36, 93, "2d6x100 pa"), (94, 100, "3d4x10 pp")],
    8: [(11, 15, "1d12x10000 pc"), (16, 29, "2d6x1000 pa"),
        (30, 87, "2d8x100 po"), (88, 100, "3d6x10 pp")],
    9: [(11, 15, "2d6x10000 pc"), (16, 29, "2d8x1000 pa"),
        (30, 85, "5d4x100 po"), (86, 100, "2d12x10 pp")],
    10: [(11, 24, "2d10x1000 pa"), (25, 79, "6d4x100 po"),
         (80, 100, "5d6x10 pp")],
    11: [(9, 14, "3d10x1000 pa"), (15, 75, "4d8x1000 po"),
         (76, 100, "4d10x10 pp")],
    12: [(9, 14, "3d12x1000 pa"), (15, 75, "1d4x1000 po"),
         (76, 100, "1d4x100 pp")],
    13: [(9, 75, "1d4x1000 po"), (76, 100, "1d10x100 pp")],
    14: [(9, 75, "1d6x1000 po"), (76, 100, "1d12x100 pp")],
    15: [(4, 74, "1d8x1000 po"), (75, 100, "3d4x100 pp")],
    16: [(4, 74, "1d12x1000 po"), (75, 100, "3d4x100 pp")],
    17: [(4, 68, "3d4x1000 po"), (69, 100, "2d10x100 pp")],
    18: [(3, 65, "3d6x1000 po"), (66, 100, "5d4x100 pp")],
    19: [(3, 65, "3d8x1000 po"), (66, 100, "3d10x100 pp")],
    20: [(3, 65, "4d8x1000 po"), (66, 100, "4d10x100 pp")],
}
_TRESON_BIENS: dict[int, tuple[int, str]] = {
    # ND → (plancher d100, formule : G=gemmes, A=objets d'art)
    1: (91, "1G"), 2: (82, "1d3G"), 3: (78, "1d3G"), 4: (71, "1d4G"),
    5: (61, "1d4G"), 6: (57, "1d4G"), 7: (49, "1d4G"), 8: (46, "1d6G"),
    9: (41, "1d8G"), 10: (36, "1d8G"), 11: (25, "1d10G"), 12: (18, "1d10G"),
    13: (12, "1d12G"), 14: (12, "2d8G"), 15: (10, "2d10G"), 16: (8, "4d6G"),
    17: (5, "4d8G"), 18: (5, "3d12G"), 19: (4, "6d6G"), 20: (3, "4d10G"),
}
_TRESON_OBJETS: dict[int, tuple[int, str]] = {
    # ND → (plancher d100, formule : f=faible, i=intermédiaire, p=puissant)
    1: (72, "1f"), 2: (50, "1f"), 3: (50, "1f"), 4: (43, "1f"),
    5: (58, "1d3f"), 6: (55, "1d3f"), 7: (52, "1d3f"), 8: (49, "1d4f"),
    9: (44, "1d4f"), 10: (41, "1d4f"), 11: (32, "1d4f"),
    12: (28, "1d6f"), 13: (20, "1d6f"), 14: (20, "1d6f"), 15: (12, "1d10f"),
    16: (41, "1d10f"), 17: (34, "1d3i"), 18: (25, "1d4i"), 19: (5, "1d4i"),
    20: (26, "1d4i"),
}

# Tables de génération des biens précieux (DRS p. « Trésors »).
_GEMMES = [
    (25, "4d4 po", "agate, azurite, hématite, lapis-lazuli, malachite, "
     "obsidienne, quartz, turquoise"),
    (50, "2d4x10 po", "calcédoine, cornaline, cristal de roche, jaspe, "
     "onyx, péridot, pierre de lune, zircon"),
    (70, "4d4x10 po", "ambre, améthyste, corail, grenat, jade, perle, "
     "spinelle, tourmaline"),
    (90, "2d4x100 po", "aigue-marine, alexandrite, perle noire, topaze"),
    (99, "4d4x100 po", "émeraude, opale, rubis, saphir"),
    (100, "2d4x1000 po", "diamant, émeraude pure, hyacinthe"),
]
_OBJETS_ART = [
    (10, "1d10x10 po", "aiguière en argent, statuette en os, bracelet d'or fin"),
    (25, "3d6x10 po", "vêtements au fil d'or, masque de velours, calice en argent"),
    (40, "1d6x100 po", "tapisserie, chope en laiton incrustée de jade"),
    (50, "1d10x100 po", "peigne en argent serti, épée plaquée d'argent"),
    (60, "2d6x100 po", "harpe sculptée, statuette d'or massif"),
    (70, "3d6x100 po", "peigne en or en forme de dragon, dague d'apparat"),
    (80, "4d6x100 po", "bandeau serti de saphirs, peinture de maître"),
    (85, "5d6x100 po", "manteau de soie orné, pendentif en saphir"),
    (90, "1d4x1000 po", "gant brodé de pierres fines, boîte à musique en or"),
    (95, "1d6x1000 po", "serre-tête en or serti d'aigue-marines, collier de perles"),
    (99, "2d4x1000 po", "couronne en or sertie de joyaux, anneau en électrum"),
    (100, "2d6x1000 po", "anneau d'or serti d'un rubis, coupe en or sertie"),
]


def _rouler_formule(formule: str, rng: random.Random) -> int:
    """« 2d8x100 po » / « 1d6x1000 pc » → montant en piéqu'unité de base."""
    f = formule.replace("po", "").replace("pc", "").replace("pa", "") \
               .replace("pp", "").strip()
    m = re.match(r"(\d+)d(\d+)(?:x(\d+))?", f)
    if not m:
        try:
            return int(f)
        except ValueError:
            return 0
    nb, faces, mult = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    return rng.randint(1, faces) * nb * mult


def _rouler_biens(n: int, type_b: str, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    for _ in range(max(1, n)):
        r = rng.randint(1, 100)
        table = _GEMMES if "G" in type_b else _OBJETS_ART
        for seuil, valeur, exemples in table:
            if r <= seuil:
                prix_txt = valeur.replace(" po", "")
                prix = _rouler_formule(valeur, rng)
                exemple = rng.choice([e.strip() for e in exemples.split(",")])
                out.append({"nom": exemple.capitalize(), "po": prix,
                            "type": "gemme" if "G" in type_b else "objet d'art"})
                break
    return out


def generer_tresor(nd: int, rng: Optional[random.Random] = None) -> dict:
    """Trésor officiel d'une rencontre de niveau `nd` (DMG 3.5) :
    3 jets d100 (pièces / biens précieux / objets). Tout est converti en
    PC (1 po = 10 pc) pour créditer directement `fiche["or"]`."""
    rng = rng or random
    nd = max(1, min(20, int(nd or 1)))
    out: dict[str, Any] = {"pc": 0, "pieces_txt": [], "biens": [],
                           "tiers_magique": [], "nd": nd}
    # 1) Pièces ---------------------------------------------------------------
    r100 = rng.randint(1, 100)
    for lo, hi, formule in _TRESON_PIECES.get(nd, []):
        if lo <= r100 <= hi:
            montant = _rouler_formule(formule, rng)
            unite = "po" if formule.endswith("po") else (
                "pp" if formule.endswith("pp") else "pc")
            # conversion en pc : 1 po = 10 pc, 1 pp = 100 pc (1 pp = 10 po)
            if unite == "po":
                montant *= 10
            elif unite == "pp":
                montant *= 100
            out["pc"] += montant
            out["pieces_txt"].append(f"{formule} = {montant} pc")
            break
    # 2) Biens précieux --------------------------------------------------------
    plancher, formule_b = _TRESON_BIENS.get(nd, (101, ""))
    if rng.randint(1, 100) >= plancher:
        nb = _rouler_formule(formule_b.split("G")[0].strip() + "d1", rng) \
            if "d" in formule_b else 1
        nb = int(re.search(r"(\d+)", formule_b).group(1)) if "d" in formule_b else 1
        out["biens"].extend(_rouler_biens(nb, "G", rng))
    # 3) Objets magiques -------------------------------------------------------
    plancher_o, formule_o = _TRESON_OBJETS.get(nd, (101, ""))
    if rng.randint(1, 100) >= plancher_o:
        n = int(re.search(r"(\d+)", formule_o).group(1)) if "d" in formule_o else 1
        for _ in range(max(1, n)):
            if "f" in formule_o:
                out["tiers_magique"].append("faible")
            elif "i" in formule_o:
                out["tiers_magique"].append("intermédiaire")
            elif "p" in formule_o:
                out["tiers_magique"].append("puissant")
    # Objets magiques : crédités en GEMMES de valeur équivalente (le moteur ne
    # fabrique pas d'objets magiques arbitraires) — voir cloturer().
    valeurs_tier = {"faible": 1000, "intermédiaire": 10000, "puissant": 40000}
    for tier in out["tiers_magique"]:
        po = valeurs_tier.get(tier, 1000)
        po = int(po * rng.uniform(0.5, 1.5))
        out["biens"].append({"nom": f"joyau d'origine magique ({tier})",
                             "po": po, "type": "bijou"})
    total_po = sum(b["po"] for b in out["biens"])
    out["pc"] += total_po * 10
    out["total_pc"] = out["pc"]
    return out


def decrire_tresor(tresor: dict) -> str:
    """Texte lisible du trésor généré (pour les events de clôture)."""
    lignes = [f"💰 **Trésor de la rencontre (ND {tresor.get('nd', '?')})** :"]
    if tresor.get("pieces_txt"):
        lignes.append("- Pièces : " + " ; ".join(tresor["pieces_txt"]))
    for b in tresor.get("biens") or []:
        lignes.append(f"- {b['nom']} ({b['type']}) — {b['po']} po")
    if tresor.get("total_pc"):
        lignes.append(f"- **Total : {tresor['total_pc']} pc**"
                      " (répartis équitablement entre les PJ vivants).")
    else:
        lignes.append("- Aucun trésor (tirage).")
    return "\n".join(lignes)
