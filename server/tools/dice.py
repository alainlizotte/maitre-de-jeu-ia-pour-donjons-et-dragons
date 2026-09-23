"""Outil Jets de dés — adapté de `Outil_JetsDes.py`.

Différences vs la version OpenWebUI :
- Plus de classe `Tools` ni de `pydantic.Valves`. La graine (seed) vient du
  contexte de la partie si besoin (extension future).
- Méthodes `def (...) -> str` devenues `@tool` async renvoyant ToolResult.
- Pas d'`__event_emitter__` : les events sont portés par ToolResult.events.
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from typing import Optional

from .base import ToolContext, ToolResult, tool


def _norm_arme(s: str) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", n).split())


# Mots-clés de SORTS/effets : un « nom d'arme » qui en contient un n'est PAS
# une arme du catalogue — la validation de dé doit alors être désactivée
# (un sort inflige ses propres dés, ex. Boule de feu 5d6).
_MOTS_SORT = (
    "sort", "sphere", "boule", "trait", "rayon", "projectile", "souffle",
    "eclair", "cone", "nuage", "flamme", "explosion", "tempete",
    "invocation", "doigt", "orbe", "malediction", "mot", "cri",
)


def _est_nom_de_sort(nom: str) -> bool:
    """Vrai si `nom` contient un mot-clé de SORT/effet : son jet de dégâts
    n'est soumis ni au catalogue ni à la fiche d'une arme (Boule de feu 5d6
    n'a pas les dés d'arme de son porteur)."""
    c = _norm_arme(nom)
    return bool(c) and any(
        re.search(r"(?:^|\s)" + re.escape(m) + r"(?:\s|$)", c)
        for m in _MOTS_SORT
    )


_ARMES_CATALOGUE: Optional[dict] = None


def _armes_catalogue() -> dict:
    """Cache local du catalogue d'armes (nom normalisé → entrée)."""
    global _ARMES_CATALOGUE
    if _ARMES_CATALOGUE is None:
        try:
            from ..catalogue import ARMES  # pylint: disable=import-outside-toplevel
            _ARMES_CATALOGUE = {_norm_arme(a.get("nom")): a for a in ARMES}
        except Exception:                                    # noqa: BLE001
            _ARMES_CATALOGUE = {}
    return _ARMES_CATALOGUE


def _de_arme_catalogue(nom: str) -> Optional[tuple[int, int, str]]:
    """(nb_des, faces, nom_canonique) des dégâts de base d'une arme du
    catalogue, ou None si `nom` ne désigne pas une arme (sort, objet…).

    Tolère les seuls qualificatifs de fabrication/matière (« de maître »,
    « +1 », « en argent ») : tout autre mot restant après retrait du nom
    d'arme (« Dague de glace » → « glace ») désactive la validation, car il
    s'agit alors d'un sort ou d'une créature, pas de l'arme du catalogue."""
    cible = _norm_arme(nom)
    if not cible:
        return None
    if _est_nom_de_sort(nom):
        return None
    cible = re.sub(r"\b(?:de maitre|masterwork|magique|enchant\w*)\b", " ", cible)
    cible = re.sub(r"\b\d+\b", " ", cible)      # bonus magique (+1 → « 1 »)
    cible = " ".join(cible.split())
    _stop = {"de", "du", "la", "le", "les", "d", "a", "au", "aux", "en",
             "acier", "fer", "argent", "adamantium", "mithral", "bois", "chene"}
    for an, arme in _armes_catalogue().items():
        if not an:
            continue
        if not re.search(r"(?:^|\s)" + re.escape(an) + r"(?:\s|$)", cible):
            continue
        reste = [w for w in cible.replace(an, " ", 1).split()
                 if w and w not in _stop]
        if reste:
            continue                    # mots étrangers → pas cette arme
        m = re.match(r"(\d+)\s*d\s*(\d+)", str(arme.get("degats") or ""))
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), str(arme.get("nom"))
    return None


def _arme_fiche_monstre(
    ctx: ToolContext, nom: str,
) -> Optional[tuple[str, int, int]]:
    """(nom d'arme, nb_des, faces) de l'arme du MONSTRE `nom` lus sur sa
    fiche bestiaire, ou None si `nom` n'est pas un monstre connu (ou si ses
    attaques sont illisibles).

    La fiche du monstre prime sur le catalogue du joueur : les créatures de
    taille G/TG manient des armes aux dés propres (Géant (froid) de taille
    G — « grande hache » 3d6+13, alors que le catalogue n'a la Grande hache
    qu'en 1d12, taille M). Valider ces jets contre le catalogue rejetait
    l'attaque automatique du bestiaire et les dégâts n'étaient JAMAIS
    appliqués. Recherche LECTURE SEULE : aucun monstre générique n'est créé
    ici (contrairement au fallback de combat._attaque_auto)."""
    if not str(nom or "").strip():
        return None
    try:
        from .monstres import _find_monstre                # noqa: PLC0415
        from ..game.combat import _arme_du_bestiaire       # noqa: PLC0415
        m = _find_monstre(ctx, str(nom))
        arme = _arme_du_bestiaire(m) if m else None
    except Exception:                                      # noqa: BLE001
        return None
    if not arme:
        return None
    return str(arme[0]), int(arme[2]), int(arme[3])


def _cite_arme(texte: str, nom_arme: str) -> bool:
    """Vrai si `texte` désigne l'arme `nom_arme` (mots entiers, accents et
    casse ignorés) : « grande hache du géant » cite « grande hache »."""
    t = _norm_arme(texte)
    a = _norm_arme(nom_arme)
    if not t or not a:
        return False
    return t == a or bool(
        re.search(r"(?:^|\s)" + re.escape(a) + r"(?:\s|$)", t)
    )



def _mod(c: int) -> int:
    """Modificateur D&D 3.5 d'une caractéristique (formule officielle (c-10)//2)."""
    return (c - 10) // 2


def _fiche_pj(ctx: ToolContext, nom: str) -> Optional[dict]:
    """Charge la fiche d'un PJ si elle existe (sinon None)."""
    try:
        from .fiches import _chemin  # pylint: disable=import-outside-toplevel
        import os as _os                             # noqa: I001
        path = _chemin(ctx, nom)
        if _os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:                                        # noqa: BLE001
        pass
    return None


def _ca_officielle(ctx: ToolContext, nom_cible: str) -> tuple[Optional[int], str]:
    """CA canonique d'une cible : fiche du PJ si joueur, sinon entrée de
    combat en cours (éventuellement ajustée temporairement), sinon bestiaire.

    Renvoie `(ca, source)` — ca=None si la cible est inconnue des sources.
    Évite que le LLM invente une CA trop basse pour toucher facilement.
    """
    fiche = _fiche_pj(ctx, nom_cible)
    if fiche is not None and fiche.get("ca") is not None:
        try:
            return int(fiche["ca"]), f"fiche de {nom_cible}"
        except (TypeError, ValueError):
            pass
    # Combat en cours : l'entrée suivie prime sur le bestiaire (elle porte
    # l'ajustement temporaire d'équilibrage — cf. engager_combat).
    try:
        import unicodedata as _ud
        nn = "".join(
            c for c in _ud.normalize("NFKD", str(nom_cible or "").lower())
            if not _ud.combining(c)
        )
        from ..game.state import PartyState  # lazy : évite les cycles
        etat_c = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
        for mo in etat_c.get("monstres_combat") or []:
            nm = "".join(
                c for c in _ud.normalize(
                    "NFKD", str(mo.get("nom") or "").lower()
                ) if not _ud.combining(c)
            )
            if nm == nn and mo.get("ca") is not None:
                tag = "ajusté, " if mo.get("_ajuste") else ""
                return int(mo["ca"]), (
                    f"combat en cours ({tag}{mo.get('nom', nom_cible)})"
                )
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from .monstres import _find_monstre   # lazy : évite les imports circulaires
        m = _find_monstre(ctx, nom_cible)
        if m is not None and m.get("ca") is not None:
            return int(m["ca"]), (
                f"bestiaire ({m.get('nom', nom_cible)}, FP {m.get('fp', '?')})"
            )
    except Exception:                                    # noqa: BLE001
        pass
    return None, ""


def _as_int(v: Any, defaut: int = 0) -> int:
    """Coerce un argument numérique du LLM (peut manquer ou arriver en str)."""
    try:
        return int(str(v).strip() or defaut)
    except (TypeError, ValueError):
        return defaut


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def lancer_d20(
    ctx: ToolContext,
    modificateur: Any = 0,
    raison: str = "",
    difficulte: Optional[int] = None,
    nom_personnage: str = "",
    competence: str = "",
) -> ToolResult:
    """
    Lance un d20 et ajoute un modificateur. Renvoie formule, jet brut, total et
    réussite/échec (si `difficulte` fournie). À utiliser pour tout jet de
    résolution D&D 3.5 (compétence, carac, sauvegarde spéciale).

    :param modificateur (int): modificateur applicable (carac + rangs + divers).
    :param raison (str): brève description du jet (ex. "Escalade d'un mur de 6 m").
    :param difficulte (int): DD à atteindre pour réussir. Optionnel.
    :param nom_personnage (str): nom du PJ qui tente le jet. Avec `competence`,
        le modificateur est recalculé depuis sa fiche (rangs + mod. carac) —
        à fournir systématiquement pour un jet de compétence.
    :param competence (str): compétence concernée (ex. "Discrétion", "Fouille").
    """
    # --- Recoupement fiche (conformité 3.5) --------------------------------
    # Un petit LLM fournit souvent modificateur=0 en ignorant les rangs et le
    # mod. de caractéristique. Si nom_personnage + competence sont donnés et
    # que la fiche contient des rangs, on recalcule rangs + mod. carac.
    modificateur = _as_int(modificateur)
    if difficulte is not None:
        difficulte = _as_int(difficulte, 10)
    mod_final = modificateur
    note_mod = ""
    if nom_personnage and competence:
        try:
            from .fiches import _chemin, bonus_dons_effet  # pylint: disable=import-outside-toplevel
            import os as _os                             # noqa: I001
            import unicodedata as _uni

            def _norm(s: str) -> str:
                nf = _uni.normalize("NFKD", (s or "").lower())
                return "".join(c for c in nf if not _uni.combining(c)).strip()

            path = _chemin(ctx, nom_personnage)
            if _os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    fiche = json.load(f)
                comps = fiche.get("competences") or {}
                rangs = 0
                for nom_c, r in comps.items():
                    if _norm(nom_c) == _norm(competence):
                        rangs = int(r or 0)
                        break
                # Dons passifs sur cette compétence (ex. Alerte → +2
                # Détection / Perception auditive) : s'applique même à
                # rang nul (guerrier avec Alerte et 0 rang en Détection).
                bonus_don = bonus_dons_effet(
                    fiche.get("dons"), "comp_" + _norm(competence)
                )
                if rangs > 0 or bonus_don:
                    from ..catalogue import COMPETENCES  # pylint: disable=import-outside-toplevel
                    cara_cle = next(
                        (c["cara"] for c in COMPETENCES
                         if _norm(c["nom"]) == _norm(competence)), "DEX",
                    )
                    val = int((fiche.get("carac") or {}).get(cara_cle, 10) or 10)
                    calc = rangs + (val - 10) // 2 + bonus_don
                    if calc != modificateur:
                        mod_final = calc
                        detail = (
                            f"{competence} {rangs} rangs + "
                            f"{cara_cle} {val} ({(val - 10) // 2:+d})"
                        )
                        if bonus_don:
                            detail += f" + {bonus_don} (dons)"
                        note_mod = (
                            f"\n- ⚠️ Modificateur recalculé {modificateur:+d} → "
                            f"{calc:+d} (fiche de {nom_personnage} : {detail})."
                        )
        except Exception:                                       # noqa: BLE001
            pass  # fiche/compétence indisponible → modificateur fourni

    jet = random.randint(1, 20)
    total = jet + mod_final
    critique = jet == 20
    fumble = jet == 1
    lignes = [
        f"🎯 **Jet de d20** — {raison}",
        f"- Jet brut : {jet}",
        f"- Modificateur : {mod_final:+d}" + note_mod,
        f"- **Total : {total}**",
    ]
    if critique:
        lignes.append("- ⭐ **20 naturel** → réussite automatique (critique éventuel).")
    if fumble:
        lignes.append("- ❌ **1 naturel** → échec automatique (maladresse).")
    if difficulte is not None and not critique and not fumble:
        ok = total >= difficulte
        lignes.append(
            f"- DD {difficulte} → " + ("✅ **Réussite**." if ok else "❌ **Échec**.")
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def calculer_initiative(ctx: ToolContext, participants: str) -> ToolResult:
    """
    Calcule l'ordre d'initiative D&D 3.5 pour un combat. Chaque participant
    obtient 1d20 + modificateur de Dextérité. Renvoie la liste triée par
    initiative décroissante, prête à afficher en début de combat.

    :param participants (str): format "Nom1:+ModDex1, Nom2:+ModDex2, …"
        (ex. "Groth:+1, Jannedarc:+0, Gobelin:+2"). Le modificateur peut être
        négatif (ex. "-1"). L'IA fournit les valeurs depuis les fiches/manuel.
    """
    try:
        items: list[tuple[str, int]] = []
        for chunk in participants.split(","):
            name, _, mod = chunk.partition(":")
            name = name.strip()
            mod = mod.strip().replace(" ", "")
            if name and mod:
                items.append((name, int(mod)))
    except ValueError as e:
        return ToolResult(
            text=f"⚠️ Format invalide : {e}\nAttendu : 'Nom1:+Mod, Nom2:-Mod, …'"
        )

    # --- Recoupement fiches PJ ---------------------------------------------
    # Pour un PJ (fiche présente), l'initiative officielle = mod. DEX + dons
    # (Initiative améliorée = +4). La valeur du LLM est ignorée si elle
    # diffère ; les monstres (sans fiche) gardent le modificateur fourni.
    notes: list[str] = []
    try:
        from .fiches import bonus_dons_effet  # pylint: disable=import-outside-toplevel
        ajustes: list[tuple[str, int]] = []
        for name, mod in items:
            fiche = _fiche_pj(ctx, name)
            if fiche is None:
                ajustes.append((name, mod))
                continue
            caracs = fiche.get("carac") or {}
            try:
                dex = int(caracs.get("DEX", 10) or 10)
            except (TypeError, ValueError):
                dex = 10
            bonus_don = bonus_dons_effet(fiche.get("dons"), "initiative")
            mod_off = (dex - 10) // 2 + bonus_don
            if mod_off != mod:
                detail = f"DEX {dex} ({(dex - 10) // 2:+d})"
                if bonus_don:
                    detail += f" + {bonus_don} (dons)"
                notes.append(
                    f"- ⚠️ {name} : initiative recalculée {mod:+d} → "
                    f"{mod_off:+d} (fiche : {detail})."
                )
                mod = mod_off
            ajustes.append((name, mod))
        items = ajustes
    except Exception:                                            # noqa: BLE001
        pass  # fiches indisponibles → modificateurs fournis

    jets = []
    for name, mod in items:
        jet = random.randint(1, 20)
        total = jet + mod
        jets.append({"nom": name, "init": total, "jet_brut": jet, "mod": mod})
    jets.sort(key=lambda x: x["init"], reverse=True)

    lignes = ["🎲 **Initiative (combat)**"]
    for i, e in enumerate(jets, 1):
        lignes.append(
            f"{i}. **{e['nom']}** — Initiative "
            f"{e['init']} (d20={e['jet_brut']}, mod={e['mod']:+d})"
        )
    lignes.extend(notes)
    lignes.append("\nOrdre : " + " → ".join(e["nom"] for e in jets))
    lignes.append("\n_C'est à la plus haute initiative d'agir la première._")
    return ToolResult(
        text="\n".join(lignes),
        # Synchro UI : on expose l'ordre pour le panneau initiative du front.
        state_patch={"initiative": jets, "courant_tour_pour": jets[0]["nom"] if jets else None},
    )


def _munition_pour_arme(arme_txt: str) -> str:
    """Munition consommée par une arme à distance (règles 3.5 : chaque tir
    dépense 1 projectile). Vide = arme de mêlée ou jet sans arme."""
    a = (arme_txt or "").lower()
    import unicodedata as _ud
    a = "".join(
        c for c in _ud.normalize("NFD", a)
        if _ud.category(c) != "Mn"
    )
    if not a:
        return ""
    if "arbalete" in a or "arbalet" in a:
        return "carreau d'arbalète"
    if "sarbacane" in a:
        return "dard"
    if "fronde" in a:
        return "balle de fronde"
    if "javelot" in a:
        return "javelot"
    if "arc" in a:
        return "flèche"
    return ""


@tool
async def lancer_attaque(
    ctx: ToolContext,
    bonus_attaque: Any = 0,
    ca_cible: Any = 10,
    nom_attaquant: str = "",
    arme: str = "",
    nom_cible: str = "",
) -> ToolResult:
    """
    Effectue un jet d'attaque D&D 3.5 contre une Classe d'Armure (CA) cible.
    Renvoie jet brut, total, et résultat (toucher / critique / maladresse / raté).
    Gère le 20 naturel (critique à confirmer) et le 1 naturel (maladresse).

    :param bonus_attaque (int): bonus total = BBA + mod. FOR (mêlée) ou
        mod. DEX (distance), lus sur la fiche du personnage — ne jamais
        inventer de bonus. Un recoupement automatique avec la fiche borne
        les valeurs manifestement erronées.
    :param ca_cible (int): Classe d'Armure de la cible. Recoupée
        automatiquement avec la fiche du PJ ou le bestiaire local — la valeur
        officielle prime toujours sur celle fournie.
    :param nom_attaquant (str): nom du personnage qui attaque.
    :param arme (str): nom de l'arme utilisée.
    :param nom_cible (str): nom de la cible.
    """
    # --- CA officielle de la cible ------------------------------------------
    # Un petit LLM « arrange » parfois la CA pour faire toucher. On impose la
    # valeur des données officielles quand la cible est connue.
    # Arguments numériques blindés (le LLM peut omettre ou envoyer "12").
    bonus_attaque = _as_int(bonus_attaque)
    ca_cible = _as_int(ca_cible, 10)

    # --- Refus des appels sans attaquant/cible -------------------------------
    # Partie dfccc120 : `lancer_attaque{}` (arguments vides) résolvait
    # « Attaque : [] vs [] (CA 10) → Touché », enregistrée comme SUCCÈS dans
    # la trace — le tour semblait résolu alors qu'aucun coup n'avait été
    # joué. Un jet d'attaque sans attaquant ou sans cible est invalide : on
    # renvoie une erreur explicite pour que le modèle ré-appelle avec les
    # vrais noms (le recoupement CA/bonus n'est possible qu'avec eux).
    if not str(nom_attaquant or "").strip() or not str(nom_cible or "").strip():
        return ToolResult(
            text=(
                "❌ Jet d'attaque invalide : nom_attaquant et nom_cible sont "
                "OBLIGATOIRES (reçus : "
                f"attaquant={nom_attaquant!r}, cible={nom_cible!r}). "
                "Rappelle lancer_attaque avec le nom du personnage, la cible, "
                "l'arme, bonus_attaque (BBA + mod. FOR/DEX de la fiche) et "
                "ca_cible."
            )
        )

    # --- Cible déjà DÉTRUITE → redirection auto (partie 263f82dc) ----------
    # Le LLM attaquait des cadavres (« le squelette 3 » détruit au round
    # précédent) : le jet se résolvait contre un ennemi hors jeu, la
    # narration contredisait l'état et les tours se perdaient. Quand la
    # cible demandée ne correspond qu'à des créatures DÉTRUITES du combat
    # en cours (exact/préfixe), l'attaque est redirigée vers la première
    # créature VIVANTE correspondante — avec note explicite.
    cible_renote = ""
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        _etat_c = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
        _mc = [m for m in (_etat_c.get("monstres_combat") or [])
               if isinstance(m, dict)]
        if _mc:
            def _nn2(s: str) -> str:
                return "".join(
                    c for c in unicodedata.normalize(
                        "NFKD", str(s or "").strip().lower()
                    ) if not unicodedata.combining(c)
                )

            def _vivante(m: dict) -> bool:
                conds = m.get("conditions") or []
                return (
                    "Détruit" not in conds and "Detruit" not in conds
                    and int(m.get("pv", 0) or 0) > 0
                )

            _nc = _nn2(nom_cible)
            _matches = [m for m in _mc if _nn2(m.get("nom")) == _nc]
            if not _matches and _nc:
                _matches = [
                    m for m in _mc
                    if (len(_nc) >= 4
                        and _nn2(m.get("nom")).startswith(_nc))
                    or (len(_nn2(m.get("nom"))) >= 4
                        and _nc.startswith(_nn2(m.get("nom"))))
                ]
            if _matches and not any(_vivante(m) for m in _matches):
                _vivantes = [m for m in _mc if _vivante(m)]
                if _vivantes:
                    _nouvelle = _vivantes[0]
                    cible_renote = (
                        f"↪️ **Cible déjà DÉTRUITE** ({nom_cible}) — attaque "
                        f"redirigée vers **{_nouvelle.get('nom')}** "
                        "(seule cible valide encore debout)."
                    )
                    nom_cible = str(_nouvelle.get("nom") or nom_cible)
    except Exception:                                           # noqa: BLE001
        pass  # hors combat / état indisponible → cible fournie

    # --- CA officielle de la cible ------------------------------------------
    # Un petit LLM « arrange » parfois la CA pour faire toucher. On impose la
    # valeur des données officielles quand la cible est connue.
    ca_off, src_ca = _ca_officielle(ctx, nom_cible)
    note_ca = ""
    if ca_off is not None:
        if ca_off != ca_cible:
            note_ca = (
                f"\n- ⚠️ CA imposée par les règles : {ca_cible} → {ca_off} "
                f"(source : {src_ca})."
            )
            ca_cible = ca_off

    # --- Recoupement fiche (conformité 3.5) --------------------------------
    # bonus_attaque = BBA + mod FOR (mêlée) / mod DEX (distance) + bonus
    # divers (arme magique, focus...). Un petit LLM invente parfois des bonus
    # absurdes (+8 au niveau 1) : on borne au bonus plausible lu sur la fiche,
    # avec une marge de +3 pour les bonus magiques temporaires.
    bonus_final = bonus_attaque
    note_bonus = ""
    note_ammo = ""
    note_degats = ""
    try:
        from .fiches import _chemin  # pylint: disable=import-outside-toplevel
        import os as _os                             # noqa: I001
        path = _chemin(ctx, nom_attaquant)
        if _os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                fiche = json.load(f)
            caracs = fiche.get("carac") or {}
            bab = int(fiche.get("bab") or 0)
            arme_l = (arme or "").lower()
            a_distance = any(
                m in arme_l for m in
                ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard", "sarbacane")
            )
            cle_car = "DEX" if a_distance else "FOR"
            val_car = int(caracs.get(cle_car, 10) or 10)
            mod_car = (val_car - 10) // 2
            plausible = bab + mod_car + 3
            if bonus_attaque > plausible:
                bonus_final = plausible
                note_bonus = (
                    f"\n- ⚠️ Bonus ajusté {bonus_attaque:+d} → {bonus_final:+d} "
                    f"(fiche de {nom_attaquant} : BBA {bab:+d}, {cle_car} "
                    f"{val_car} ({mod_car:+d}) + marge +3 max pour bonus divers)."
                )
            # 💪 Bonus de dégâts OFFICIEL (partie 263f82dc : le LLM passait
            # +4 puis +6 pour le MÊME attaquant — bonus improvisé au lieu du
            # calcul 3.5). Mêlée = mod. FOR, ×1,5 (arrondi vers le bas) pour
            # une arme à deux mains ; distance = +0 (mod. DEX ne s'applique
            # pas aux dégâts). Le LLM recopie CE bonus dans lancer_degats.
            if not a_distance:
                deux_mains = any(
                    m in arme_l for m in ("deux mains", "2 mains")
                )
                bonus_deg_off = mod_car * 3 // 2 if deux_mains else mod_car
                detail_deg = (
                    f"FOR {val_car} ({mod_car:+d})"
                    + (" ×1,5 arme à deux mains" if deux_mains else "")
                )
                note_degats = (
                    f"\n- 💪 **Bonus dégâts officiel : {bonus_deg_off:+d}** "
                    f"({detail_deg}) — recopie CE bonus dans `lancer_degats` "
                    "(jamais un bonus improvisé)."
                )
            else:
                note_degats = (
                    "\n- 💪 **Bonus dégâts officiel : +0** (arme à distance : "
                    "le mod. DEX ne s'applique pas aux dégâts) — recopie CE "
                    "bonus dans `lancer_degats`."
                )
            # 🏹 Munition auto (a6d11005, règles d'usage) : chaque tir à
            # distance déduit 1 projectile de l'inventaire du PJ — sans
            # dépendre de l'appel (oublié) à inventaire_consommer_munition.
            munition = _munition_pour_arme(arme)
            if munition:
                from .inventaire import _consommer_fragment
                consomme = _consommer_fragment(fiche, munition, 1)
                if consomme:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(fiche, f, ensure_ascii=False, indent=2)
                    reste = next(
                        (int(e.get("qte", 1) or 1)
                         for e in fiche.get("inventaire") or []
                         if str(e.get("nom", "")).lower() == consomme.lower()),
                        0,
                    )
                    note_ammo = (
                        f"\n- 🏹 **1 × {consomme}** consommée — "
                        + (f"restantes : {reste}" if reste
                           else "plus de munitions !")
                    )
                else:
                    note_ammo = (
                        f"\n- ⚠️ Aucune {munition} dans l'inventaire — tir "
                        "résolu cette fois, mais réapprovisionne-toi "
                        "(inventaire_ajouter) : sans munition, plus de tir."
                    )
    except Exception:                                           # noqa: BLE001
        pass  # fiche absente (monstre ?) → on trust le bonus fourni

    jet = random.randint(1, 20)
    total = jet + bonus_final
    lignes = [
        f"⚔️ **Attaque** : {nom_attaquant} [{arme}] vs {nom_cible} (CA {ca_cible})",
        f"- Jet brut d'attaque : {jet}",
        f"- Bonus total : {bonus_final:+d}" + note_bonus + note_ca + note_ammo,
        f"- **Total attaque : {total}**",
    ]
    if cible_renote:
        lignes.append(cible_renote)
    if note_degats:
        lignes.append(note_degats.strip().lstrip("\n"))
    if jet == 20:
        lignes.append(
            "- ⭐ **20 naturel** → toucher automatique + menace de critique "
            "(effectuer un second jet d'attaque pour confirmer ; si réussi, "
            "dégâts doublés/triplés selon arme)."
        )
    elif jet == 1:
        lignes.append(
            "- ❌ **1 naturel** → maladresse : attaque ratée automatiquement "
            "(conséquences possibles : arme lâchée, etc.)."
        )
    else:
        ok = total >= ca_cible
        lignes.append(
            f"- CA {ca_cible} → " + ("✅ **Touché**." if ok else "❌ **Manqué**.")
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_degats(
    ctx: ToolContext,
    nb_des: int,
    faces: int,
    bonus: int,
    arme_ou_sort: str,
    cible: str,
    attaquant: str = "",
) -> ToolResult:
    """
    Effectue le jet de dégâts D&D 3.5 selon la formule NdF + bonus. Renvoie les
    jets bruts, le total, et un résumé lisible.

    :param nb_des (int): nombre de dés (ex. 1, 2, 3…).
    :param faces (int): type de dé (4, 6, 8, 10, 12, 20).
    :param bonus (int): bonus de dégâts (mod. FOR, magie, etc.). Peut être négatif.
    :param arme_ou_sort (str): nom de l'arme ou du sort.
    :param cible (str): nom de la cible.
    :param attaquant (str): nom du personnage ou du monstre ATTAQUANT
        (optionnel). Si c'est un monstre du bestiaire, les dés de SON arme
        sont validés contre sa fiche (tailles spéciales) ; sinon contre le
        catalogue d'armes.
    """
    nb_des = max(1, _as_int(nb_des, 1))
    faces = _as_int(faces, 6)
    bonus = _as_int(bonus)
    if faces not in (2, 3, 4, 6, 8, 10, 12, 20, 100):
        return ToolResult(text=f"⚠️ Type de dé {faces} non standard en D&D 3.5.")
    # Conformité des dés — deux sources de vérité, dans l'ordre :
    # 1. la FICHE du monstre `attaquant`, quand `arme_ou_sort` nomme SON
    #    arme (tailles spéciales : le Géant (froid) de taille G frappe en
    #    « grande hache » 3d6 — pas 1d12 comme au catalogue, taille M) ;
    # 2. à défaut, le CATALOGUE d'armes (partie 4b529064 : la « Hache à
    #    deux mains » — 1d12 au catalogue — fut résolue en 1d20+4).
    # Un nom de sort désactive les deux (Boule de feu 5d6 n'est pas une arme).
    if not _est_nom_de_sort(arme_ou_sort):
        _fiche = _arme_fiche_monstre(ctx, attaquant)
        if _fiche is not None and _cite_arme(arme_ou_sort, _fiche[0]):
            _nom_f, _nb_f, _fa_f = _fiche
            if (nb_des, faces) != (_nb_f, _fa_f):
                return ToolResult(text=(
                    f"⛔ **Formule de dégâts non conforme à la fiche du "
                    f"monstre** : « {attaquant} » inflige {_nb_f}d{_fa_f} "
                    f"avec « {_nom_f} » (fiche bestiaire), pas "
                    f"{nb_des}d{faces}.\n"
                    f"Relance `lancer_degats` avec nb_des={_nb_f}, "
                    f"faces={_fa_f} (le bonus reste inchangé)."
                ))
        else:
            _arme = _de_arme_catalogue(arme_ou_sort)
            if _arme is not None:
                _nb, _fa, _nom_canon = _arme
                if (nb_des, faces) != (_nb, _fa):
                    return ToolResult(text=(
                        f"⛔ **Formule de dégâts non conforme au catalogue** : "
                        f"« {arme_ou_sort} » inflige {_nb}d{_fa} (D&D 3.5, "
                        f"« {_nom_canon} »), pas {nb_des}d{faces}.\n"
                        f"Relance `lancer_degats` avec nb_des={_nb}, "
                        f"faces={_fa} (le bonus de Force reste inchangé)."
                    ))
    jets = [random.randint(1, faces) for _ in range(nb_des)]
    total = max(0, sum(jets) + bonus)  # jamais de dégâts négatifs (min 0)
    lignes = [
        f"💥 **Dégâts** : {arme_ou_sort} → {cible}",
        f"- Formule : {nb_des}d{faces}{'+' if bonus >= 0 else ''}{bonus}",
        f"- Jets bruts : {jets}",
        f"- Total jets : {sum(jets)}",
        f"- Bonus dégâts : {bonus:+d}",
        f"- **Dégâts infligés : {total}**",
    ]
    if sum(jets) + bonus < 0:
        lignes.append(
            "- ℹ️ Total négatif ramené à 0 (les dégâts ne soignent pas la cible)."
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_sauvegarde(
    ctx: ToolContext,
    type_sauvegarde: str,
    modificateur: int,
    difficulte: int,
    nom_personnage: str,
    source: str,
) -> ToolResult:
    """
    Effectue un jet de sauvegarde D&D 3.5 contre une difficulté DD.

    :param type_sauvegarde (str): "Vigueur", "Réflexes" ou "Volonté".
    :param modificateur (int): total du jet de sauvegarde de base + carac.
        Recoupé avec la fiche du PJ si elle existe (la valeur officielle prime).
    :param difficulte (int): DD à atteindre (souvent 10 + ½ niveau + mod carac).
    :param nom_personnage (str): nom du personnage qui sauvegarde.
    :param source (str): source du danger (sort, piège, poison…).
    """
    t = str(type_sauvegarde or "").lower().strip()
    # Normalisation accents : « Volonté » → « volonte », « Réflexes » →
    # « reflexes » — sans cela, la forme accentuée (documentée ci-dessus !)
    # ne matchait JAMAIS les clés de fiche « Volonte »/« Reflexes » et le
    # recoupement fiche échouait en silence.
    t = "".join(
        c for c in unicodedata.normalize("NFKD", t)
        if not unicodedata.combining(c)
    )
    if t not in ("vigueur", "reflexes", "volonte"):
        return ToolResult(
            text=(
                f"⚠️ type_sauvegarde invalide : '{type_sauvegarde}'. "
                "Attendu : Vigueur, Réflexes ou Volonté."
            )
        )
    aliases = {"reflexes": "Reflexes", "volonte": "Volonte"}
    cle = aliases.get(t, t.capitalize())
    label = {"Vigueur": "Vigueur", "Reflexes": "Réflexes", "Volonte": "Volonté"}.get(cle, cle)
    modificateur = _as_int(modificateur)
    difficulte = _as_int(difficulte, 10)

    # --- Recoupement fiche PJ ----------------------------------------------
    # La fiche stocke les totaux officiels (base de classe + mod. carac) :
    # on les utilise plutôt que le chiffre approximatif du LLM, puis on
    # ajoute les dons passifs de sauvegarde (Volonté de fer +2 Volonté,
    # Grande Fortitude +2 Vigueur, Réflexes surprenants +2 Réflexes).
    mod_final = int(modificateur)
    note_mod = ""
    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is not None:
        sauv = fiche.get("sauvegardes") or {}
        for k, v in sauv.items():
            if str(k).strip().lower().rstrip("s") == cle.strip().lower().rstrip("s"):
                try:
                    off = int(v)
                    try:
                        from .fiches import bonus_dons_effet  # pylint: disable=import-outside-toplevel
                        bonus_don = bonus_dons_effet(
                            fiche.get("dons"), "sauvegarde_" + cle.lower()
                        )
                    except Exception:                        # noqa: BLE001
                        bonus_don = 0
                    off_total = off + bonus_don
                    if off_total != mod_final:
                        detail = f"{label} {off:+d}"
                        if bonus_don:
                            detail += f" + {bonus_don} (dons)"
                        note_mod = (
                            f"\n- ⚠️ Modificateur recalculé {modificateur:+d} → "
                            f"{off_total:+d} (fiche de {nom_personnage} : "
                            f"{detail})."
                        )
                        mod_final = off_total
                    break
                except (TypeError, ValueError):
                    break

    jet = random.randint(1, 20)
    total = jet + mod_final
    ok = (total >= difficulte or jet == 20) and jet != 1
    lignes = [
        f"🛡️ **Sauvegarde** ({label}) — {nom_personnage} vs {source} (DD {difficulte})",
        f"- Jet brut : {jet}",
        f"- Modificateur : {mod_final:+d}" + note_mod,
        f"- **Total : {total}**",
    ]
    if jet == 20:
        lignes.append("- ⭐ **20 naturel** → réussite automatique.")
    elif jet == 1:
        lignes.append(
            "- ❌ **1 naturel** → échec automatique (conséquences aggravées possibles)."
        )
    else:
        lignes.append(
            "- DD " + str(difficulte) + " → "
            + ("✅ **Réussite**." if ok else "❌ **Échec**.")
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_caracteristiques(ctx: ToolContext, methode: str = "4d6_garder_3") -> ToolResult:
    """
    Tire les 6 caractéristiques D&D 3.5 (FOR, DEX, CON, INT, SAG, CHA) selon la
    méthode demandée. Utile à la création de personnage.

    :param methode (str): une parmi —
      "4d6_garder_3" (4d6, garder les 3 meilleurs, ×6) [défaut recommandé],
      "3d6" (3d6, ×6, dans l'ordre),
      "repartition_elite" (15,14,13,12,10,8 à répartir librement — DMG n°3),
      "achat_points" (grille de coûts DMG n°2, budget 25 points).
    """
    methodes = {
        "4d6_garder_3": "4d6, garder les 3 meilleurs, six fois",
        "3d6": "3d6, six fois, dans l'ordre",
        "repartition_elite": (
            "Répartition d'élite (DMG n°3) — 6 valeurs fixes "
            "[15,14,13,12,10,8] à répartir librement entre les 6 carac."
        ),
        "achat_points": (
            "Achat de points (DMG n°2) — budget 25 pts à répartir "
            "selon la grille de coûts 8→18."
        ),
    }
    if methode not in methodes:
        return ToolResult(text="⚠️ Méthode inconnue. Options : " + ", ".join(methodes.keys()))

    noms = ["FOR", "DEX", "CON", "INT", "SAG", "CHA"]
    lignes = [f"🎲 **Caractéristiques** — méthode : {methodes[methode]}"]

    if methode == "4d6_garder_3":
        for nom in noms:
            quatre = [random.randint(1, 6) for _ in range(4)]
            trois = sorted(quatre, reverse=True)[:3]
            somme = sum(trois)
            lignes.append(
                f"  - **{nom}** : {somme} (mod {_mod(somme):+d}) "
                f"[4d6 = {quatre} → garder {trois}]"
            )
    elif methode == "3d6":
        for nom in noms:
            trois = [random.randint(1, 6) for _ in range(3)]
            somme = sum(trois)
            lignes.append(f"  - **{nom}** : {somme} (mod {_mod(somme):+d}) [3d6 = {trois}]")
    elif methode == "repartition_elite":
        valeurs = [15, 14, 13, 12, 10, 8]
        lignes.append(
            "Répartir librement ces 6 valeurs entre FOR, DEX, CON, INT, SAG, CHA "
            "(méthode DMG n°3 « Répartition d'élite »)."
        )
        for nom, v in zip(noms, valeurs):
            lignes.append(f"  - valeur {v} disponible (mod {_mod(v):+d})")
    elif methode == "achat_points":
        couts = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 6, 15: 8, 16: 10, 17: 13, 18: 16}
        budget = 25
        lignes.append(f"**Achat de points (DMG n°2)** — budget : **{budget} points**.")
        lignes.append("Grille de coûts (valeur → coût en points) :")
        for v, c in couts.items():
            lignes.append(f"  - {v:2d} → {c:2d} pts")
        lignes.append(
            f"Répartir les {budget} points entre FOR, DEX, CON, INT, SAG, CHA en "
            "respectant cette grille. La table officielle impose une valeur "
            "minimale de 8 au départ."
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_des(
    ctx: ToolContext,
    nb_des: int,
    faces: int,
    bonus: int = 0,
    raison: str = "lancer générique",
) -> ToolResult:
    """
    Utilitaire générique : lance nb_des dés de `faces` faces, ajoute un bonus,
    renvoie le détail. À utiliser quand aucune méthode spécialisée ne s'applique
    (pourcentage, table aléatoire, percentile d20 d10…).

    :param nb_des (int): nombre de dés (≥1).
    :param faces (int): nombre de faces (ex. 4, 6, 8, 10, 12, 20, 100).
    :param bonus (int): bonus à ajouter (peut être négatif).
    :param raison (str): court descriptif du jet.
    """
    if nb_des < 1:
        return ToolResult(text="⚠️ nb_des doit être ≥ 1.")
    if faces < 2:
        return ToolResult(text="⚠️ faces doit être ≥ 2.")
    jets = [random.randint(1, faces) for _ in range(nb_des)]
    total = sum(jets) + bonus
    lignes = [
        f"🎲 **{nb_des}d{faces}{'+' if bonus >= 0 else ''}{bonus}** — {raison}",
        f"- Jets bruts : {jets}",
        f"- Total jets : {sum(jets)}",
        f"- Bonus : {bonus:+d}",
        f"- **Total : {total}**",
    ]
    return ToolResult(text="\n".join(lignes))
