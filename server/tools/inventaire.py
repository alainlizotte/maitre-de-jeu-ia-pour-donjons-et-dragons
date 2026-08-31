"""Outil Inventaire & Charge — gestion d'inventaire et d'encombrement D&D 3.5.

Permet de suivre la charge transportée par un personnage de façon MÉCANIQUE et
conforme au Manuel du Joueur 3.5 :
  - inventaire structuré : liste d'objets `{nom, qte}` (même forme que le champ
    `equipement` des fiches existantes, avec un poids par unité issu d'un
    catalogue d'équipement officiel ou fourni explicitement) ;
  - poids transporté recalculé automatiquement à chaque modification (kg) ;
  - catégorie d'encombrement calculée d'après la table de charge du PHB 3.5
    (Légère ≤ ⅓ de la charge max, Moyenne ≤ ⅔, Lourde ≤ max, Dépassée au-delà) ;
  - consommation d'ammunition (flèches, balles de fronde, carreaux) quand un PJ
    tire à distance.

Les fiches peuvent être créées soit par le formulaire (qui écrit `charge_max`),
soit par les tools LLM `fiche_perso_creer*` (qui n'écrivent PAS `charge_max`).
Pour être robuste, on recalcule toujours la capacité max à la volée depuis la
Force + la catégorie de taille de la race, en écrivant `charge_max` si absent.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
_LB_EN_KG = 0.4536


def _norm(s: Any) -> str:
    """Normalise un nom d'objet : minuscule, sans accent, singulier approx."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    # Singulier grossier (retire le 's' final / 'aux' → 'au') pour matcher
    # « flèches » avec « flèche ».
    if len(s) > 2 and s.endswith(("aux", "eaux")):
        s = s[:-3] + "au"
    elif len(s) > 2 and s.endswith("s"):
        s = s[:-1]
    return s


def _chemin_fiche(ctx: ToolContext, nom: str) -> Optional[str]:
    """Chemin vers la fiche (résolution PJ/pseudo-joueur comme dans fiches.py)."""
    try:
        from .fiches import _chemin  # pylint: disable=import-outside-toplevel
        return _chemin(ctx, nom)
    except Exception:                                                # noqa: BLE001
        return None


def _charge_max(fiche: dict[str, Any]) -> int:
    """Charge max transportable (kg) d'une fiche — Force + catégorie de taille.

    Priorité à `charge_max` déjà écrit (formulaire) ; sinon on le calcule via
    `persos.charge_maximale`. Pour une fiche sans race connue, taille Moyenne.
    """
    cm = fiche.get("charge_max")
    if cm:
        try:
            return int(cm)
        except (TypeError, ValueError):
            pass
    try:
        from ..persos import RACES, charge_maximale, resoudre_race  # pylint: disable=import-outside-toplevel
        race_canon = resoudre_race(str(fiche.get("race") or "")) or \
            str(fiche.get("race") or "")
        taille = (RACES.get(race_canon, {}) or {}).get("taille", "M")
        for_ = int((fiche.get("carac") or {}).get("FOR", 10) or 10)
        return int(charge_maximale(for_, taille))
    except Exception:                                                # noqa: BLE001
        # Repli : Force 10, taille Moyenne→ charge max ~90 kg (PHB 3.5).
        for_ = int((fiche.get("carac") or {}).get("FOR", 10) or 10)
        return int(round((for_ * 10 * 0.4536) * 1.0))


# --------------------------------------------------------------------------- #
#  Catalogue d'équipement (PHB 3.5)
# --------------------------------------------------------------------------- #
# Poids officiels D&D 3.5 (Manuel du Joueur, chapitre Équipement) en livres,
# convertis en kg (arrondis à 2 décimales). Clés normalisées (singulier, sans
# accent). `qte_pour_poids` = nombre d'unités pour le poids indiqué (les
# munitions sont vendues par lots : 20 flèches, 10 balles de fronde…).
_LIVRES = 0.4536


def _kg(lb: float, qte_pour_poids: int = 1) -> dict[str, Any]:
    """Construit l'entrée catalogue `{poids_kg, lot}` depuis un poids en livres."""
    return {"poids_kg": round(lb * _LIVRES, 2), "lot": qte_pour_poids}


_POIDS_OFFICIELS: dict[str, dict[str, Any]] = {
    # --- Munitions (vendues par lots, poids par lot officiel PHB) ----------
    "fleche":       _kg(3.0, qte_pour_poids=20),    # 20 flèches = 3 lb
    "carreau":      _kg(1.0, qte_pour_poids=10),    # 10 carreaux = 1 lb
    "balle de fronde": _kg(3.0, qte_pour_poids=10), # 10 balles = 3 lb
    "balle de fronde": _kg(3.0, qte_pour_poids=10),
    "pierre de fronde": _kg(0.0, qte_pour_poids=1),
    "balle":        _kg(3.0, qte_pour_poids=10),
    # --- Armes de corps à corps -------------------------------------------
    "arc":          _kg(2.0),                        # arc court
    "arc court":    _kg(2.0),
    "arc long":     _kg(3.0),
    "grande epee":  _kg(8.0),                         # grande épée
    "espadon":      _kg(8.0),
    "grande hache": _kg(12.0),
    "hache a deux main": _kg(12.0),
    "epee courte":  _kg(2.0),
    "epee longue":  _kg(4.0),
    "rapiere":      _kg(2.0),
    "dague":        _kg(1.0),
    "baton":        _kg(4.0),                         # bâton (quarterstaff)
    "matraque":     _kg(3.0),
    "gourdin":      _kg(3.0),
    "faucille":     _kg(2.0),
    "masse":        _kg(8.0),                         # masse d'armes lourde
    "masse legere": _kg(4.0),
    "masse d armes": _kg(8.0),
    "masse d armes lourde": _kg(8.0),
    "masse d armes legere": _kg(4.0),
    "marteau de guerre": _kg(5.0),
    "hache d arme": _kg(6.0),
    "hache":        _kg(3.0),                         # hachette
    "hachette":     _kg(3.0),
    "fleau d arme": _kg(10.0),
    "hallebarde":   _kg(12.0),
    "glaive":       _kg(10.0),
    "javelot":      _kg(2.0),
    "javeline":     _kg(2.0),
    "lance":        _kg(6.0),                         # lance (spear)
    "lance courte": _kg(3.0),
    "lanterne a capuchon": _kg(2.0),                  # objet équipement
    "arbalete legere": _kg(4.0),
    "arbalete lourde": _kg(8.0),
    "fronde":       _kg(0.0),
    "cimeterre":    _kg(4.0),
    "flamberge":    _kg(8.0),
    "tronconneuse": _kg(8.0),
    # --- Armures et boucliers (poids pour une taille Moyenne) -------------
    "armure rembourree": _kg(10.0),
    "armure de cuir": _kg(15.0),
    "cuir cloute":  _kg(20.0),
    "chemise de maille": _kg(25.0),
    "cuir epai":    _kg(25.0),
    "armure d ecaille": _kg(30.0),
    "cotte de maille": _kg(40.0),
    "plastron":     _kg(30.0),
    "harnois complet": _kg(50.0),
    "targe":        _kg(5.0),
    "bouclier bois leger": _kg(5.0),
    "bouclier bois lourd": _kg(10.0),
    # --- Équipement d'aventurier -------------------------------------------
    "sac a dos":    _kg(2.0),
    "torche":       _kg(1.0),
    "outre":        _kg(4.0),
    "outre a eau":  _kg(4.0),
    "rations de voyage": _kg(1.0),
    "ration":       _kg(1.0),
    "ration journaliere": _kg(1.0),
    "corde":        _kg(10.0),                        # corde 15 m
    "corde de chanvre": _kg(10.0),
    "couverture":   _kg(5.0),
    "sac de couchage": _kg(5.0),
    "lit de camp":  _kg(5.0),
    "pierre a feu": _kg(0.0),
    "briquet":      _kg(0.0),
    "luth":         _kg(3.0),
    "instrument":   _kg(3.0),
    "grimoire":     _kg(3.0),
    "livre de sorts": _kg(3.0),
    "composantes":  _kg(2.0),
    "potion de soins legers": _kg(0.5),
    "potion":       _kg(0.5),
    "bandages":     _kg(1.0),
    "trousse de soins": _kg(1.0),
    "kit premiers secours": _kg(1.0),
    "pics":         _kg(5.0),
    "piolet":       _kg(5.0),
    "couteau de survie": _kg(1.0),
    "lanterne":     _kg(2.0),
    "huile":        _kg(1.0),
    "cartes":       _kg(0.5),
    "messager":     _kg(0.0),
    "bourse":       _kg(0.5),
    "gibeciere":    _kg(0.5),
    "relique":      _kg(1.0),
    "statuette":    _kg(1.0),
    "amulette":     _kg(0.1),
    "clef":         _kg(0.1),
    "cle":          _kg(0.1),
    "pierre a aiguiser": _kg(1.0),
    "savon":        _kg(0.5),
    "craie":        _kg(0.1),
}

# Monnaie : 50 pièces de même type = 1 lb (PHB 3.5).
_MONNAIE_PC_POIDS = round(50.0 * _LIVRES, 2)  # poids de 50 pc


def _poids_unitaire(nom: str, explicite: Optional[float]) -> Optional[float]:
    """Poids (kg) d'une unité d'objet : poids explicite sinon catalogue.

    Pour une munition du catalogue, on divise le poids du lot par la taille de
    lot (ex. 20 flèches = 3 lb → 0,15 lb ≈ 0,07 kg chacune).
    """
    if explicite is not None:
        try:
            return max(0.0, float(explicite))
        except (TypeError, ValueError):
            return None
    info = _POIDS_OFFICIELS.get(_norm(nom))
    if info:
        lot = int(info.get("lot") or 1)
        return round(float(info["poids_kg"]) / lot, 4)
    # Objet inconnu du catalogue → poids inconnu (None) ; le MJ doit fournir
    # un poids explicite pour ne pas fausser l'encombrement.
    return None


def _taille_monnaie(quantite_pc: int) -> float:
    """Poids (kg) de `quantite_pc` pièces de cuivre (50 pc = 1 lb)."""
    return round(quantite_pc / 50.0 * _LIVRES, 3)


# --------------------------------------------------------------------------- #
#  Lecture / calcul d'encombrement
# --------------------------------------------------------------------------- #
def _inventaire(fiche: dict[str, Any]) -> list[dict[str, Any]]:
    """Inventaire structuré : le champ `inventaire` si présent, sinon on le
    dérive de `equipement` (même forme `{nom, qte}`). Chaîne → liste d'objets."""
    inv = fiche.get("inventaire")
    if isinstance(inv, list):
        return [i for i in inv if isinstance(i, dict)]
    equip = fiche.get("equipement") or []
    if isinstance(equip, str):
        equip = _parse_lignes_equip(equip)
    return [{"nom": e.get("nom", ""), "qte": int(e.get("qte", 1) or 1)}
            for e in equip if isinstance(e, dict) and e.get("nom")]


def _parse_lignes_equip(texte: str) -> list[dict[str, Any]]:
    """Parse un texte "Épée x2, Corde" vers [{nom, qte}] (rétrocompat)."""
    objets: list[dict[str, Any]] = []
    for chunk in str(texte or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.search(r"x(\d+)$", chunk, re.IGNORECASE)
        if m:
            objets.append({"nom": chunk[:m.start()].strip(), "qte": int(m.group(1))})
        else:
            objets.append({"nom": chunk, "qte": 1})
    return objets


def _recalculer_charge(fiche: dict[str, Any],
                       monnaie: bool = True) -> tuple[float, str, int]:
    """Calcule poids transporté (kg) + catégorie d'encombrement D&D 3.5.

    Retourne `(poids_kg, catégorie, charge_max_kg)`.
    Catégories (PHB 3.5) : Légère ≤ ⅓ max, Moyenne ≤ ⅔ max, Lourde ≤ max,
    Dépassée au-delà.
    """
    inv = _inventaire(fiche)
    poids = 0.0
    inconnus: list[str] = []
    for e in inv:
        nom = e.get("nom", "")
        qte = int(e.get("qte", 1) or 1)
        pu = _poids_unitaire(nom, e.get("poids"))
        if pu is None:
            inconnus.append(nom)
            continue
        poids += pu * qte
    if monnaie:
        pc = int(fiche.get("or", 0) or 0)
        if pc:
            poids += _taille_monnaie(pc)

    max_kg = _charge_max(fiche)
    tiers = max_kg / 3.0
    # Catégories ASCII (sans accent), conformes au schéma de fiche.
    if poids <= tiers:
        cat = "Legere"
    elif poids <= 2 * tiers:
        cat = "Moyenne"
    elif poids <= max_kg:
        cat = "Lourde"
    else:
        cat = "Depassee"
    return round(poids, 2), cat, max_kg


# Étiquette d'affichage (accentuée) d'une catégorie canonique ASCII.
_LIBELLE_ENCUMBRANCE = {"Legere": "Légère", "Moyenne": "Moyenne",
                        "Lourde": "Lourde", "Depassee": "Dépassée"}


def _libelle_cat(cat: str) -> str:
    return _LIBELLE_ENCUMBRANCE.get(cat, cat)


def _syncroniser(ctx: ToolContext, nom: str, fiche: dict[str, Any],
                 poids: float, cat: str, max_kg: int) -> dict[str, Any]:
    """Sauvegarde la fiche et répercute poids/encombrement dans l'état PJ.

    Renvoie le `state_patch` à diffuser (signal `pj_updated` + path patches).
    Lève `ValueError` si la fiche ne respecte pas le schéma (échec de
    sauvegarde) — l'appelant le convertit en `ToolResult` d'erreur.
    """
    patch = {"pj_updated": nom}
    idx = None
    from .fiches import _save_fiche, _sync_pj  # pylint: disable=import-outside-toplevel
    _save_fiche(ctx, nom, fiche)
    champs = {
        "poids_transporte": float(poids),
        "etat_encumbrance": cat,
        "charge_max": int(max_kg),
    }
    try:
        idx = _sync_pj(ctx, nom, champs)
    except Exception:                                    # noqa: BLE001
        idx = None  # synchro PJ best-effort (partie absente…) — non bloquant
    if idx is not None:
        for k, v in champs.items():
            patch[f"pj.{idx}.{k}"] = v
    return patch


def _finaliser(ctx: ToolContext, nom: str,
               fiche: dict[str, Any]) -> tuple[Optional[float],
                                               Optional[str],
                                               Optional[int],
                                               Optional[dict],
                                               Optional[ToolResult]]:
    """Recalcule la charge, synchronise et rend explicite tout échec.

    Retourne `(poids, cat, max_kg, state_patch, err)`. Si la sauvegarde de
    fiche échoue (schéma D&D 3.5 non respecté → `ValueError`), `err` contient
    un `ToolResult` d'erreur à renvoyer tel quel à l'utilisateur (au lieu de
    masquer l'échec en succès silencieux).
    """
    try:
        poids, cat, max_kg = _recalculer_charge(fiche)
        patch = _syncroniser(ctx, nom, fiche, poids, cat, max_kg)
        return poids, cat, max_kg, patch, None
    except ValueError as e:                             # noqa: BLE001
        return None, None, None, None, ToolResult(
            text=f"❌ Échec de sauvegarde de la fiche de {nom} :\n{e}")


def _format_inventaire(fiche: dict[str, Any], poids: float, cat: str,
                       max_kg: int) -> str:
    lignes = [f"🧺 **Inventaire & charge de {fiche.get('nom', '?')}**"]
    inv = _inventaire(fiche)
    if not inv:
        lignes.append("- _(vide)_")
    for e in inv:
        nom = e.get("nom", "?")
        qte = int(e.get("qte", 1) or 1)
        pu = _poids_unitaire(nom, e.get("poids"))
        if pu is None:
            lignes.append(f"- {nom} ×{qte} — poids inconnu (fournir un poids)")
        else:
            lignes.append(
                f"- {nom} ×{qte} — {round(pu * qte, 2)} kg "
                f"({pu:.3f} kg / unité)"
            )
    pc = int(fiche.get("or", 0) or 0)
    if pc:
        lignes.append(f"- Or : {pc} pc — {_taille_monnaie(pc)} kg")
    pct = round(poids / max_kg * 100, 1) if max_kg else 0
    lignes.append(
        f"\n⚖️ **Charge transportée : {poids} kg / {max_kg} kg "
        f"({pct}%) — encombrement : {_libelle_cat(cat)}.**"
    )
    lignes.append(
        "_Règles (PHB 3.5) : Légère ≤ ⅓ de la charge max, Moyenne ≤ ⅔, "
        "Lourde ≤ max. Une charge Lourde limite les déplacements et donne un "
        "malus DEX/compétences ; Dépassée interdit d'avancer._"
    )
    return "\n".join(lignes)


def _charger_fiche(ctx: ToolContext, nom: str) -> tuple[Optional[dict], Optional[ToolResult]]:
    try:
        from .fiches import _load_fiche   # pylint: disable=import-outside-toplevel
        fiche = _load_fiche(ctx, nom)
    except Exception as e:                                        # noqa: BLE001
        return None, ToolResult(text=f"❌ Erreur fiche : {e}")
    if fiche is None:
        return None, ToolResult(
            text=f"❌ Aucune fiche trouvée pour '{nom}' — crée le personnage "
            "d'abord (fiche_perso_creer / fiche_perso_creer_rapide)."
        )
    return fiche, None


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def inventaire_consulter(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Affiche l'inventaire et la charge transportée d'un personnage : liste des
    objets (avec quantité et poids), poids total (kg), capacité maximale et
    catégorie d'encombrement D&D 3.5 (Légère / Moyenne / Lourde / Dépassée).
    À utiliser pour vérifier les munitions (ex. flèches restantes) ou le
    fardeau d'un PJ avant/reprès ramassage.

    :param nom (str): nom du personnage ou du joueur qui l'incarne.
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    poids, cat, max_kg = _recalculer_charge(fiche)
    return ToolResult(
        text=_format_inventaire(fiche, poids, cat, max_kg),
        state_patch={"pj_updated": nom},
    )


@tool
async def inventaire_ajouter(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    poids: Optional[float] = None,
) -> ToolResult:
    """
    Ajoute un objet à l'inventaire d'un personnage et recalcule la charge.

    Le poids par unité est pris dans le catalogue d'équipement D&D 3.5
    (PHB) si l'objet est connu (flèches, armes, sac à dos, corde…) ; pour un
    objet hors catalogue (trésor de donjon inventé), fournissez `poids`
    (kg/unité) pour ne pas fausser l'encombrement. Les quantités d'un même
    objet s'additionnent (ex. 20 + 10 flèches = 30).

    :param nom (str): nom du personnage (ou du joueur qui l'incarne).
    :param objet (str): nom de l'objet (ex. "flèche", "émeraude", "relique").
    :param quantite (int): nombre d'unités à ajouter (défaut 1).
    :param poids (float): poids en kg d'UNE unité (optionnel, sinon catalogue).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    if not objet or not _norm(objet):
        return ToolResult(text="❌ Donne un nom d'objet valide.")
    qte = max(1, int(quantite or 1))
    pu = _poids_unitaire(objet, poids)
    if pu is None:
        return ToolResult(
            text=(
                f"⚠️ Objet « {objet} » inconnu du catalogue d'équipement "
                "(PHB 3.5) : fournis `poids` (kg par unité) pour que la charge "
                "soit correctement comptée — sinon l'encombrement sera faux."
            )
        )

    inv = _inventaire(fiche)
    cible = _norm(objet)
    fusionne = False
    for e in inv:
        if _norm(e.get("nom")) == cible:
            neuf = int(e.get("qte", 1) or 1) + qte
            e["qte"] = neuf
            if e.get("poids") is None:
                e["poids"] = pu
            fusionne = True
            break
    if not fusionne:
        inv.append({"nom": objet, "qte": qte, "poids": pu})

    fiche["inventaire"] = inv
    # Aligne le champ d'affichage legacy `equipement` sur l'inventaire.
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err
    return ToolResult(
        text=(
            f"✅ **{qte} × {objet}** ajouté(s) à l'inventaire de "
            f"{fiche.get('nom', nom)} ({round(pu * qte, 2)} kg).\n\n"
            + _format_inventaire(fiche, poids, cat, max_kg)
        ),
        state_patch=patch,
    )


@tool
async def inventaire_retirer(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    poids: Optional[float] = None,
) -> ToolResult:
    """
    Retire un objet de l'inventaire d'un personnage (ou en réduit la quantité)
    et recalcule la charge transportée.

    :param nom (str): nom du personnage (ou du joueur qui l'incarne).
    :param objet (str): nom de l'objet à retirer.
    :param quantite (int): nombre d'unités à retirer (défaut 1).
    :param poids (float): poids en kg par unité (optionnel, sinon catalogue).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    qte = max(1, int(quantite or 1))
    cible = _norm(objet)
    inv = _inventaire(fiche)
    trouve = None
    for e in inv:
        if _norm(e.get("nom")) == cible:
            trouve = e
            break
    if trouve is None:
        return ToolResult(
            text=f"❌ « {objet} » n'est pas dans l'inventaire de {nom}."
        )
    reste = int(trouve.get("qte", 1) or 1) - qte
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)

    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err
    return ToolResult(
        text=(
            f"🗑️ **{qte} × {objet}** retiré(s) de l'inventaire de "
            f"{fiche.get('nom', nom)}.\n\n"
            + _format_inventaire(fiche, poids, cat, max_kg)
        ),
        state_patch=patch,
    )


@tool
async def inventaire_consommer_munition(
    ctx: ToolContext,
    nom: str,
    munition: str,
    quantite: int = 1,
) -> ToolResult:
    """
    Consomme de l'ammunition (flèche, carreau, balle de fronde…) après un tir
    à distance : réduit la quantité en inventaire et met à jour la charge.
    Idéal après `lancer_attaque`/`lancer_degats` sur un adversaire à l'arc ou
    à la fronde.

    :param nom (str): nom du personnage.
    :param munition (str): "flèche" | "carreau" | "balle de fronde" | ….
    :param quantite (int): nombre de munitions tirées (défaut 1).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    if not _poids_unitaire(munition, None):
        return ToolResult(
            text=f"⚠️ « {munition} » n'est pas une munition connue du catalogue."
        )
    qte = max(1, int(quantite or 1))
    cible = _norm(munition)
    inv = _inventaire(fiche)
    trouve = None
    for e in inv:
        if _norm(e.get("nom")) == cible:
            trouve = e
            break
    if trouve is None:
        return ToolResult(
            text=(
                f"❌ {nom} n'a plus de {munition} en inventaire — il ne peut "
                "pas tirer à distance ! (recharge via inventaire_ajouter)"
            )
        )
    dispo = int(trouve.get("qte", 0) or 0)
    if dispo < qte:
        return ToolResult(
            text=(
                f"❌ {nom} n'a que {dispo} {munition} — impossible d'en "
                f"dépenser {qte}."
            )
        )
    reste = dispo - qte
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)

    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err
    return ToolResult(
        text=(
            f"🏹 **{qte} {munition}(s)** consommée(s) par {fiche.get('nom', nom)} "
            f"— restantes : {trouve['qte'] if reste > 0 else 0}.\n\n"
            + _format_inventaire(fiche, poids, cat, max_kg)
        ),
        state_patch=patch,
    )


@tool
async def inventaire_ramasser(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    poids: Optional[float] = None,
    source: str = "",
) -> ToolResult:
    """
    Ramasse un objet trouvé dans le donjon (trésor, butin, relique, monnaie…)
    et l'ajoute à l'inventaire en recalculant la charge. À utiliser quand le
    MJ octroie un objet trouvé pendant l'exploration ou après un combat.

    :param nom (str): nom du personnage qui ramasse.
    :param objet (str): nom de l'objet (ex. "émeraude", "bourse de 25 pc",
        "clé", "potion de soins légers").
    :param quantite (int): nombre d'unités (défaut 1).
    :param poids (float): poids en kg par unité si objet hors catalogue.
    :param source (str): d'où vient l'objet (ex. "salle du trésor", "gobelin 1").
    """
    return await inventaire_ajouter(
        ctx, nom, objet, quantite=quantite, poids=poids
    )


def _normaliser_fiche(fiche: dict[str, Any]) -> dict[str, Any]:
    """Rappelle les champs dérivés d'encombrement pour un affichage homogène."""
    poids, cat, max_kg = _recalculer_charge(fiche)
    fiche.setdefault("poids_transporte", round(poids, 2))
    fiche["poids_transporte"] = round(poids, 2)
    fiche["etat_encumbrance"] = cat
    fiche["charge_max"] = int(max_kg)
    return fiche
