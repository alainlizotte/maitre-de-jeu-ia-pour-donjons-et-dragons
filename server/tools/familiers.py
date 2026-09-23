"""Tools familier / compagnon animal D&D 3.5.

`appeler_familier` : matérialise en jeu le familier (Magicien/Sorcier) ou le
compagnon animal (Druide/Rodeur) CHOISI à la création de la fiche (champ
`familier`). Application des règles PHB 3.5 (regles-donjons-dragons.com
pages 60/64 et 48) :
- familier : l'appel exige un rituel d'une journée + 100 po de composantes ;
  le profil est AJUSTÉ au maître (PV = moitié des siens, CA + armure
  naturelle, BBA du maître + meilleur mod For/Dex, pouvoirs par niveau) ;
- compagnon animal : pas de coût, profil ajusté au niveau de classe
  (DV sup., Aj. For/Dex, tours, pouvoirs).

Si un combat est EN COURS, le compagnon rejoint l'ordre d'initiative comme
allié (PV suivis mécaniquement dans `monstres_combat`). Hors combat, sa
présence est simplement actée (il suit son maître) ; rappeler le tool au
début d'un combat pour l'insérer dans l'initiative.

`renvoyer_familier` : renvoi/mort du familier — jet de Vigueur DD 15 du
maître, échec = −200 XP par niveau (réussite : moitié), jamais sous 0 XP.
"""

from __future__ import annotations

import random
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


def _norm(s: str) -> str:
    import unicodedata

    nf = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in nf if not unicodedata.combining(c)).strip()


def _fiche_pj(ctx: ToolContext, nom: str):
    from .fiches import _load_fiche

    return _load_fiche(ctx, nom)


def _charge_persos():
    from .. import familiers as fam
    from .. import persos as persos_mod

    return fam, persos_mod


def _label_libre(labels: list[str], base: str) -> str:
    """Suffixe « (2) », « (3) »… si un homonyme est déjà sur le plateau."""
    cle = base.strip().lower()
    dernier = 0
    for ex in labels:
        exl = str(ex).strip().lower()
        if exl == cle:
            dernier = max(dernier, 1)
        elif exl.startswith(cle + " ("):
            try:
                dernier = max(
                    dernier,
                    int(exl[len(cle) + 2 :].split(")")[0].strip()),
                )
            except ValueError:
                pass
    return base if dernier == 0 else f"{base} ({dernier + 1})"


def _resume_profil(profil: dict[str, Any]) -> str:
    sauves = profil.get("sauvegardes") or {}
    lignes = [
        f"- PV **{profil['pv']}** · CA **{profil['ca']}** · BBA d'attaque "
        f"**+{profil['bba_attaque']}** · DV effectifs {profil['dv']}",
        f"- Sauvegardes : Vig {sauves.get('Vigueur', 0):+d}, "
        f"Réf {sauves.get('Reflexes', 0):+d}, Vol {sauves.get('Volonte', 0):+d}",
    ]
    if profil.get("degats") and profil["degats"] != "—":
        lignes.append(f"- Attaques : {profil['degats']}")
    if profil.get("faculte_maitre"):
        lignes.append(f"- Faculté transmise au maître : {profil['faculte_maitre']}")
    pouvoirs = profil.get("pouvoirs") or []
    if pouvoirs:
        lignes.append(f"- Pouvoirs : {', '.join(pouvoirs)}")
    if profil.get("tours"):
        lignes.append(
            f"- Tours supplémentaires connus : {profil['tours']} "
            "(au choix du maître, sans dressage)"
        )
    if profil.get("for"):
        lignes.append(
            f"- For {profil['for']} · Dex {profil['dex']} (ajustements de "
            "progression inclus)"
        )
    return "\n".join(lignes)


@tool
async def appeler_familier(
    ctx: ToolContext,
    nom_personnage: str,
    ignorer_cout: bool = False,
) -> ToolResult:
    """
    Appelle le FAMILIER (Magicien/Sorcier) ou le COMPAGNON ANIMAL (Druide/
    Rodeur) choisi à la création de la fiche. Règles PHB 3.5 : premier appel
    du familier = rituel d'un jour + 100 po (déduits de l'or de la fiche,
    sauf ignorer_cout=true) ; compagnon animal : aucun coût. Le profil est
    ajusté au niveau du maître (PV, CA, attaques, pouvoirs). Si un combat est
    en cours, le compagnon rejoint l'initiative comme allié (PV suivis
    mécaniquement) ; hors combat il suit simplement son maître (rappelle le
    tool au début d'un combat).

    :param nom_personnage (str): nom du PJ propriétaire (fiche avec champ
        `familier` — sinon refus : l'espèce se choisit à la création).
    :param ignorer_cout (bool): True = passer outre les 100 po de composantes
        (cadeau, composantes trouvées…). Défaut: False.
    """
    from .fiches import _save_fiche, _sync_pj, _patch_pj

    fam, _persos_mod = _charge_persos()

    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom_personnage}'.")
    nom = str(fiche.get("nom") or nom_personnage)
    classe = str(fiche.get("classe") or "")
    niveau = max(1, int(fiche.get("niveau") or 1))

    type_attendu = fam.type_compagnon_classe(classe)
    if type_attendu is None:
        return ToolResult(
            text=(f"⛔ **{classe}** n'a ni familier ni compagnon animal "
                  f"(PHB 3.5 : Magicien/Sorcier → familier ; Druide → "
                  f"compagnon animal dès le niv.1 ; Rodeur → niv.4).")
        )
    info = fiche.get("familier")
    if not isinstance(info, dict) or not info.get("espece"):
        return ToolResult(
            text=(f"⛔ **{nom}** n'a pas d'espèce enregistrée. L'espèce se "
                  f"CHOISIT à la création du personnage (formulaire, section "
                  f"« {type_attendu.capitalize()} ») — modifie la fiche ou "
                  "raconte le rituel narrativement SANS inventer de stats.")
        )
    if info.get("type") and info["type"] != type_attendu:
        return ToolResult(
            text=(f"⛔ Incohérence : la fiche de **{nom}** ({classe}) porte un "
                  f"{info['type']}, pas un {type_attendu}.")
        )

    espece = fam.trouver_espece(str(info["espece"]))
    if espece is None:
        return ToolResult(text=f"❌ Espèce inconnue : « {info['espece']} ».")
    if type_attendu == "compagnon":
        niv_min = fam.NIVEAU_MIN_COMPAGNON.get(classe, 1)
        if niveau < niv_min:
            return ToolResult(
                text=(f"⛔ Le {classe} obtient son compagnon animal au niveau "
                      f"{niv_min} ({nom} est niv.{niveau}).")
            )
    animal = fam.charger_animal(ctx.data_dir, espece["cle"])
    if animal is None:
        return ToolResult(
            text=(f"❌ Espèce « {espece['nom']} » absente du bestiaire local "
                  "(stats indisponibles).")
        )

    # ── Prérequis du rituel (familier uniquement) ──────────────────────────
    lignes: list[str] = []
    if type_attendu == "familier" and not bool(info.get("invoque")):
        cout_pc = 1000  # 100 po de composantes matérielles (PHB 3.5)
        or_fiche = int(fiche.get("or") or 0)
        if not ignorer_cout and or_fiche < cout_pc:
            return ToolResult(
                text=(f"⛔ **Appel refusé** : le rituel d'appel du familier "
                      f"exige des composantes valant **100 po** — {nom} n'a "
                      f"que {or_fiche} pc ({or_fiche // 10} po). Le "
                      "personnage doit rassembler les composantes (ou le MJ "
                      "relance avec ignorer_cout=true si elles sont "
                      "offertes/trouvées).")
            )
        if not ignorer_cout:
            fiche["or"] = or_fiche - cout_pc
            lignes.append(
                f"- 💰 Composantes du rituel : −100 po (reste "
                f"{fiche['or']} pc)."
            )
        lignes.append(
            "- 🕯️ Rituel accompli : une journée d'incantations et 100 po de "
            "composantes — le lien est scellé (PHB 3.5)."
        )
    elif type_attendu == "compagnon" and not bool(info.get("invoque")):
        lignes.append(
            "- 🌿 Le compagnon animal répond au lien druidique : aucun "
            "rituel matériel n'est requis (PHB 3.5)."
        )
    info["invoque"] = True
    info["type"] = type_attendu
    info["espece"] = espece["nom"]
    fiche["familier"] = info
    _save_fiche(ctx, nom, fiche)

    profil = fam.profil(fiche, animal)
    nom_creature = f"{espece['nom']} de {nom}"
    lignes.insert(0, (
        f"✨ **{nom_creature}** répond à l'appel de **{nom}** "
        f"({classe} niv.{niveau}) — {type_attendu} lié à son maître."
    ))
    lignes.append(_resume_profil(profil))
    lignes.append(
        "- Les pouvoirs ne fonctionnent qu'à moins de 1,5 km du maître ; si "
        "le familier meurt ou est renvoyé : jet de Vigueur DD 15 du maître "
        "(échec : −200 XP/niveau, réussite : moitié) et remplacement "
        "impossible avant un an et un jour."
    )

    # ── Combat en cours → insertion dans l'initiative comme allié ─────────
    from .state import _party

    state = _party(ctx)
    etat = state.load()
    patch: dict[str, Any] | None = None
    if etat.get("phase") == "combat" and etat.get("initiative"):
        try:
            mod_init = int(str(animal.get("init", "0")).replace("+", "").strip())
        except ValueError:
            mod_init = 0
        jet = random.randint(1, 20)
        total = jet + mod_init
        labels = [str(e.get("nom", "")) for e in etat["initiative"]]
        label = _label_libre(labels, nom_creature)
        entree = {"nom": label, "init": total, "jet_brut": jet, "mod": mod_init}
        ordre = list(etat["initiative"])
        ordre.append(entree)
        ordre.sort(key=lambda x: x.get("init", 0), reverse=True)
        monstres = list(etat.get("monstres_combat") or [])
        monstres.append({
            "nom": label,
            "pv": int(profil["pv"]),
            "pv_max": int(profil["pv"]),
            "ca": int(profil["ca"]),
            "fp": str(animal.get("fp", "?")),
            "conditions": [],
            "allie": True,
        })
        etat["initiative"] = ordre
        etat["monstres_combat"] = monstres
        err = state.save(etat)
        if err:
            return ToolResult(text=err)
        patch = {"initiative": ordre, "monstres_combat": monstres}
        position = ordre.index(entree) + 1
        lignes.append(
            f"- ⚔️ **{label}** rejoint le combat comme ALLIÉ — initiative "
            f"{total} (d20={jet}, mod={mod_init:+d}), agira en {position}e "
            "position. PV suivis mécaniquement : "
            "fiche_perso_infliger_degats si subit des dégâts."
        )
    else:
        lignes.append(
            "- Le compagnon suit son maître. Au début d'un combat, rappelle "
            "`appeler_familier` pour l'insérer dans l'initiative."
        )

    idx = _sync_pj(ctx, nom, {})
    return ToolResult(
        text="\n".join(lignes),
        state_patch=_patch_pj(nom, idx, {}) if idx is not None else patch,
    )


@tool
async def renvoyer_familier(ctx: ToolContext, nom_personnage: str) -> ToolResult:
    """
    RENVOIE le familier / compagnon animal du personnage (libre volonté ou
    conséquence narrative). Règles PHB 3.5 (familier, pages 60/64) : le
    maître joue un jet de Vigueur DD 15 — échec : perte de 200 XP par niveau
    (réussite : moitié), jamais sous 0 XP ; remplacement impossible avant un
    an et un jour (à gérer narrativement). Le compagnon est retiré du combat
    s'il y figurait (condition « Détruit »).

    :param nom_personnage (str): nom du PJ propriétaire.
    """
    from .fiches import _save_fiche, _sync_pj, _patch_pj

    fam, persos_mod = _charge_persos()

    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom_personnage}'.")
    nom = str(fiche.get("nom") or nom_personnage)
    info = fiche.get("familier")
    if not isinstance(info, dict) or not info.get("espece") or not bool(
        info.get("invoque")
    ):
        return ToolResult(
            text=f"⛔ **{nom}** n'a pas de familier/compagnon invoqué à renvoyer."
        )

    niveau = max(1, int(fiche.get("niveau") or 1))
    classe = str(fiche.get("classe") or "")

    lignes = [f"👋 **{info['espece']} de {nom}** est renvoyé par son maître."]
    if classe in fam.CLASSES_FAMILIER:
        carac = fiche.get("carac") or {}
        try:
            mod_con = (int(carac.get("CON", 10)) - 10) // 2
        except (TypeError, ValueError):
            mod_con = 0
        base_vig = (
            2 + niveau // 2
            if "Vigueur" in (persos_mod.CLASSES.get(classe, {}).get("sauves_bonnes") or [])
            else niveau // 3
        )
        jet = random.randint(1, 20)
        total = jet + mod_con + base_vig
        perte = 200 * niveau
        if total >= 15:
            perte //= 2
            lignes.append(
                f"- 🛡️ Jet de Vigueur DD 15 : d20={jet} + {base_vig:+d} (base) "
                f"{mod_con:+d} (Con) = **{total}** — RÉUSSITE : perte d'XP "
                f"réduite de moitié."
            )
        else:
            lignes.append(
                f"- 💥 Jet de Vigueur DD 15 : d20={jet} + {base_vig:+d} (base) "
                f"{mod_con:+d} (Con) = **{total}** — ÉCHEC."
            )
        xp = int(fiche.get("xp") or 0)
        perte = min(perte, xp)  # jamais sous 0 XP
        fiche["xp"] = xp - perte
        lignes.append(f"- XP : −{perte} (reste {fiche['xp']}).")
        lignes.append(
            "- ⏳ Un familier tué ou renvoyé ne peut être remplacé avant un "
            "an et un jour (PHB 3.5)."
        )
    info["invoque"] = False
    fiche["familier"] = info
    _save_fiche(ctx, nom, fiche)

    # Retrait du plateau s'il combattait (condition « Détruit » = exclu).
    from .state import _party

    state = _party(ctx)
    etat = state.load()
    cible = f"{info['espece']} de {nom}".strip().lower()
    modifies = False
    for mo in etat.get("monstres_combat") or []:
        n = str(mo.get("nom", "")).strip().lower()
        if n == cible or n.startswith(cible + " ("):
            if "Détruit" not in (mo.get("conditions") or []):
                mo.setdefault("conditions", []).append("Détruit")
                modifies = True
    if modifies:
        state.save(etat)
        lignes.append(
            "- ⚔️ Le compagnon quitte l'ordre d'initiative (compté « Détruit »)."
        )

    idx = _sync_pj(ctx, nom, {"xp": fiche["xp"]})
    return ToolResult(
        text="\n".join(lignes),
        state_patch=_patch_pj(nom, idx, {"xp": fiche["xp"]})
        if idx is not None
        else None,
    )
