"""Système d'expérience et de progression D&D 3.5 (règles officielles).

- Table « Experience Point Awards » du DMG 3.5 (p.38) : XP gagnés PAR
  personnage selon le niveau du PJ et le facteur de puissance (FP/CR) du
  monstre vaincu — les CR < 1 (1/2, 1/3…) suivent la règle officielle de
  fraction de la colonne CR 1.
- Seuils de niveau : un personnage de niveau N possède au minimum
  N×(N-1)/2 × 1000 XP cumulés (PHB 3.5, table 3-2).
- Montée de niveau : jet réel du dé de vie de la classe + mod. CON.
- Perte de niveau (energy drain permanent) : l'XP est ramenée au point
  médian du niveau inférieur (DMG 3.5 « Level Loss »).
"""

from __future__ import annotations

import random
from typing import Any, Optional

# --------------------------------------------------------------------------- #
#  Table officielle DMG 3.5 p.38 — « Experience Point Awards »
#
#  Construction officielle (vérifiée sur toutes les valeurs publiées de la
#  table) : XP(niveau n, CR c) = BASE(c) / FACTEUR(n), où :
#  - BASE = ligne « niveau 1 » de la table (progression officielle par CR) ;
#  - FACTEUR double tous les 2 niveaux à partir du niveau 5 :
#    1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, …, 1024 (niveau 20).
#  Invariants garantis : la diagonale (CR = niveau) vaut 300 partout et un
#  groupe de 4 PJ monte d'un niveau toutes les ~13 rencontres équivalentes
#  (DMG 3.5 « Awarding Experience »).
#  NB : le Manuel arrondit quelques cellules « joliment » (2100 pour 2133) ;
#  on utilise ici l'arrondi standard au supérieur (int(x+0.5)).
# --------------------------------------------------------------------------- #
_BASE_CR1_20 = [
    300, 600, 900, 1200, 1800, 2400, 3600, 4800, 6400, 9600,
    12800, 19200, 25600, 38400, 51200, 76800, 102400, 153600, 230400, 307200,
]
_FACTEUR_NIVEAU = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192,
                   256, 384, 512, 768, 1024]

# CR fractionnaires officiels : fraction de la colonne CR 1 (règle DMG 3.5).
_CR_FRACTIONS: dict[str, float] = {
    "1/2": 0.5, "1/3": 1 / 3, "1/4": 0.25, "1/6": 1 / 6,
    "1/8": 0.125, "1/10": 0.1,
}

_NIVEAU_MAX = 20


def parse_cr(fp: Any) -> Optional[float]:
    """Convertit un FP du bestiaire (« 1/2 », « 3 », 3.0) en float."""
    if fp is None:
        return None
    s = str(fp).strip().replace(",", ".")
    if s in _CR_FRACTIONS:
        return _CR_FRACTIONS[s]
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def xp_pour_cr(fp: Any, niveau: int) -> int:
    """XP gagnés par un PJ de `niveau` pour un monstre de FP `fp`
    (table DMG 3.5). 0 si les données sont invalides.
    """
    cr = parse_cr(fp)
    if cr is None or niveau < 1:
        return 0
    n = min(niveau, _NIVEAU_MAX)          # au-delà de 20 : ligne 20
    if cr < 1:                             # CR fractionnaire officiel
        base = _BASE_CR1_20[0] * cr
    elif cr <= _NIVEAU_MAX:
        base = _BASE_CR1_20[int(round(cr)) - 1]
    else:
        # Approximation épique : ×2 par cran de CR au-delà de 20.
        base = _BASE_CR1_20[-1] * (2 ** (int(round(cr)) - _NIVEAU_MAX))
    brut = base / _FACTEUR_NIVEAU[n - 1]
    return int(brut + 0.5) if brut >= 1 else (1 if brut >= 0.5 else 0)


def xp_min_niveau(niveau: int) -> int:
    """XP cumulés minimum pour être de niveau `niveau` (PHB 3.5)."""
    if niveau <= 1:
        return 0
    return niveau * (niveau - 1) // 2 * 1000


def xp_prochain_niveau(niveau: int, xp: int) -> int:
    """XP manquants pour atteindre le niveau suivant."""
    return max(0, xp_min_niveau(niveau + 1) - int(xp))


# --------------------------------------------------------------------------- #
#  Progression d'une fiche (dict fiche en place — pas d'IO ici)
# --------------------------------------------------------------------------- #
def _de_vie_classe(classe: Any) -> int:
    """Dé de vie officiel de la classe (PHB 3.5) ; 6 par défaut."""
    try:
        from ..tools.fiches import _CLASSES_35  # lazy (évite cycle import)
        return _CLASSES_35.get(str(classe or "").strip().lower(), (6,))[0]
    except Exception:                                        # noqa: BLE001
        return 6


def _mod_con(fiche: dict[str, Any]) -> int:
    try:
        con = int((fiche.get("carac") or {}).get("CON", 10) or 10)
    except (TypeError, ValueError):
        con = 10
    return (con - 10) // 2


def _bonus_dons_pv(dons: Any, niveau: int) -> int:
    """Bonus de PV apporté par les dons d'un personnage (PHB 3.5 finales).

    - « Dur à cuire » / Toughness : +3 PV (flat).
    - « Vigueur surhumaine » / Improved Toughness : +1 PV par niveau.
    - « Robustesse » : +1 PV par niveau.
    Déjà appliqué à la création ; cette fonction sert à re-synchroniser
    `pv_max` quand le niveau change (montée ou perte).
    """
    if not dons:
        return 0
    if isinstance(dons, str):
        import json as _json
        try:
            dons = _json.loads(dons)
        except _json.JSONDecodeError:
            dons = [x.strip() for x in dons.split(",")]
    if not isinstance(dons, (list, tuple)):
        return 0
    import unicodedata as _u
    def _norm(s: str) -> str:
        s = _u.normalize("NFKD", (s or "").lower())
        s = "".join(c for c in s if not _u.combining(c))
        return s.replace("-", " ").replace("'", " ").strip()
    lvl = max(1, int(niveau or 1))
    bonus = 0
    for d in dons:
        nom = _norm(str(d or ""))
        if not nom:
            continue
        if any(k in nom for k in ("dur a cuire", "toughness", "resilient", "endurci", "coriace")):
            bonus += 3
        elif any(k in nom for k in ("vigueur surhumaine", "improved toughness", "grande robustesse", "vigueur")):
            bonus += lvl
        elif any(k in nom for k in ("robustesse",)):
            bonus += lvl
    return bonus


def _resync_dons_pv(fiche: dict[str, Any], new_niveau: int, old_bonus: int) -> tuple[int, int]:
    """Recalcule le bonus de PV des dons après un changement de niveau.

    Renvoie (pv_max, pv) recalibrés pour tenir compte des dons qui évoluent
    avec le niveau (ex. Vigueur surhumaine +1/niveau). `old_bonus` est le bonus
    déjà compté dans pv_max (avant remontée/descente).
    """
    new_bonus = _bonus_dons_pv(fiche.get("dons"), new_niveau)
    delta = new_bonus - old_bonus
    if not delta:
        return int(fiche.get("pv_max", 1)), int(fiche.get("pv", 1))
    pv_max = max(1, int(fiche.get("pv_max", 1)) + delta)
    pv = min(int(fiche.get("pv", 1)) + delta, pv_max)
    return pv_max, pv


def appliquer_gain(
    fiche: dict[str, Any], montant: int, rng: Optional[random.Random] = None
) -> list[str]:
    """Ajoute `montant` XP à la fiche et applique les montées de niveau
    successives (jet réel du dé de vie + mod. CON par niveau gagné).

    Mutate `fiche` en place ; renvoie la liste des lignes de journal
    (gain + montées de niveau + rappels dons/caractéristiques).
    """
    rng = rng or random
    montant = int(montant)
    logs: list[str] = []
    if montant == 0:
        return logs
    niveau = int(fiche.get("niveau", 1) or 1)
    xp = int(fiche.get("xp", 0) or 0)
    if montant < 0:
        xp = max(0, xp + montant)
        fiche["xp"] = xp
        logs.append(f"{fiche.get('nom', '?')} : {montant:+d} XP → {xp} XP.")
        # Perte de niveau si l'XP tombe sous le minimum du niveau actuel
        # (règle DMG 3.5 : on ne perd un niveau que si le total descend
        # sous le plancher du niveau courant — le jeu officiel utilise le
        # midpoint via perte_niveau ; ici on applique le plancher).
        while niveau > 1 and xp < xp_min_niveau(niveau):
            niveau -= 1
            dv = _de_vie_classe(fiche.get("classe"))
            perte = max(1, dv // 2 + 1 + _mod_con(fiche))
            fiche["niveau"] = niveau
            fiche["pv_max"] = max(1, int(fiche.get("pv_max", dv)) - perte)
            fiche["pv"] = min(int(fiche.get("pv", 1)), fiche["pv_max"])
            pv_max_new, pv_new = _resync_dons_pv(fiche, niveau, _bonus_dons_pv(fiche.get("dons"), niveau + 1))
            fiche["pv_max"] = pv_max_new
            fiche["pv"] = pv_new
            logs.append(
                f"⚠️ {fiche.get('nom', '?')} tombe au niveau {niveau} "
                f"(XP sous le minimum) : -{perte} PV max."
            )
        return logs

    xp += montant
    fiche["xp"] = xp
    logs.append(
        f"{fiche.get('nom', '?')} gagne {montant} XP → {xp} XP "
        f"(niveau {niveau}, prochain niveau à {xp_min_niveau(niveau + 1)})."
    )
    # Bonus de PV des dons au niveau courant (avant montées), pour recaler les
    # dons qui évoluent avec le niveau (Vigueur surhumaine/Robustesse +1/niv.).
    bonus_dons_avant = _bonus_dons_pv(fiche.get("dons"), niveau)
    # Montées de niveau successives (XP peut franchir plusieurs niveaux).
    while niveau < 40 and xp >= xp_min_niveau(niveau + 1):
        niveau += 1
        dv = _de_vie_classe(fiche.get("classe"))
        jet = rng.randint(1, dv)
        gain_pv = max(1, jet + _mod_con(fiche))
        fiche["niveau"] = niveau
        fiche["pv_max"] = int(fiche.get("pv_max", dv)) + gain_pv
        fiche["pv"] = int(fiche.get("pv", 1)) + gain_pv
        # Recaler le bonus de PV des dons qui évoluent avec le niveau
        pv_max_new, pv_new = _resync_dons_pv(fiche, niveau, bonus_dons_avant)
        bonus_dons_avant = _bonus_dons_pv(fiche.get("dons"), niveau)
        fiche["pv_max"] = pv_max_new
        fiche["pv"] = pv_new
        logs.append(
            f"🎉 {fiche.get('nom', '?')} monte au NIVEAU {niveau} ! "
            f"+{gain_pv} PV (1d{dv}={jet} + CON {_mod_con(fiche):+d}) → "
            f"{fiche['pv']}/{fiche['pv_max']} PV."
        )
        if niveau % 4 == 0:
            logs.append(
                f"📈 Niveau multiple de 4 : {fiche.get('nom', '?')} doit "
                "ajouter +1 à une caractéristique (choix du joueur)."
            )
        if niveau % 3 == 0:
            logs.append(
                f"🎁 {fiche.get('nom', '?')} gagne un don au niveau {niveau}."
            )
    return logs


def appliquer_perte_niveau(fiche: dict[str, Any], nb: int = 1) -> list[str]:
    """Perte PERMANENTE de `nb` niveau(x) — energy drain définitif ou effet
    magique (DMG 3.5 « Level Loss ») : l'XP est ramenée au point médian du
    nouveau niveau et les PV du niveau sont retirés.
    """
    logs: list[str] = []
    for _ in range(max(0, int(nb))):
        niveau = int(fiche.get("niveau", 1) or 1)
        if niveau <= 1:
            fiche["xp"] = 0
            logs.append(
                f"💀 {fiche.get('nom', '?')} perd son dernier niveau — "
                "XP ramenée à 0."
            )
            break
        nouveau = niveau - 1
        milieu = (xp_min_niveau(nouveau) + xp_min_niveau(niveau)) // 2
        dv = _de_vie_classe(fiche.get("classe"))
        perte = max(1, dv // 2 + 1 + _mod_con(fiche))
        fiche["niveau"] = nouveau
        fiche["xp"] = milieu
        fiche["pv_max"] = max(1, int(fiche.get("pv_max", dv)) - perte)
        fiche["pv"] = min(int(fiche.get("pv", 1)), fiche["pv_max"])
        pv_max_new, pv_new = _resync_dons_pv(fiche, nouveau, _bonus_dons_pv(fiche.get("dons"), niveau))
        fiche["pv_max"] = pv_max_new
        fiche["pv"] = pv_new
        logs.append(
            f"⚠️ {fiche.get('nom', '?')} perd un niveau : niveau {nouveau}, "
            f"XP ramenée au milieu du niveau ({milieu}), -{perte} PV max."
        )
    return logs


def niveau_effectif(fiche: dict[str, Any]) -> int:
    """Niveau effectif = niveau - niveaux négatifs (energy drain)."""
    niveau = int(fiche.get("niveau", 1) or 1)
    neg = int(fiche.get("niveaux_negatifs", 0) or 0)
    return max(0, niveau - neg)


def ligne_xp_fiche(fiche: dict[str, Any]) -> str:
    """Ligne « XP » officielle affichée sur la fiche du personnage."""
    niveau = int(fiche.get("niveau", 1) or 1)
    xp = int(fiche.get("xp", 0) or 0)
    neg = int(fiche.get("niveaux_negatifs", 0) or 0)
    requis = xp_min_niveau(niveau + 1)
    ligne = f"- XP : {xp} / {requis} (prochain niveau : {xp_prochain_niveau(niveau, xp)} XP restants)"
    if neg:
        ligne += (
            f"\n- ⚠️ Niveaux négatifs : {neg} (niveau effectif "
            f"{niveau_effectif(fiche)} — sauvegarde par jour ou "
            "Restauration, sinon perte définitive du niveau)."
        )
    return ligne
