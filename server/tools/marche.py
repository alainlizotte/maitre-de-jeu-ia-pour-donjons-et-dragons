"""Outils Marché & Auberge — commerce et restauration en ville (PHB 3.5).

La disponibilité, le prix et le niveau des services dépendent du TYPE DE
PEUPLEMENT de la localité où se trouve le groupe (Hameau → Métropole,
`server/villes.py`). La localité courante est celle de l'état de partie
(`etat.lieu.nom`) ; le paramètre `ville` permet de la devancer.

MONNAIE (convention projet) : la fiche stocke `or` en PC (pièces de cuivre).
1 po = 10 pc, 1 pa = 1 pc, la pièce de cuivre = 0,1 pc (prix minimum 1 pc).
Tous les prix affichés sont en pc ; le libellé officiel (« 2 po ») reste
indiqué pour la narration.

Effets RÈGLES-MAISON de l'auberge (documentés) :
  - repas (toute qualité)  → retire la condition « Affamé » ;
  - repas de bonne qualité → ajoute la condition « Rassasié » ;
  - logement de bonne qualité → marqueur `fiche.auberge` : le PROCHAIN
    repos_long soigne +ceil(niveau/2) PV (≈ +50 %) et retire « fatigue » /
    « épuisé » (le marqueur est consommé au repos).
Les règles officielles (PHB 3.5) n'attribuent AUCUN effet mécanique aux repas
et logements : il s'agit de règles-maison du projet.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .. import equipement_phb as phb
from .. import villes
from .base import ToolContext, ToolResult, tool
from .inventaire import (_cle_objet, _format_inventaire, _inventaire,
                         _finaliser, _charger_fiche, _norm,
                         _normaliser_fiche)


# --------------------------------------------------------------------------- #
#  Helpers localité / argent
# --------------------------------------------------------------------------- #
def _lieu_nom(ctx: ToolContext) -> str:
    try:
        from ..game.state import PartyState
        etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
        return str((etat.get("lieu") or {}).get("nom") or "").strip()
    except Exception:                                    # noqa: BLE001
        return ""


def _ville(ctx: ToolContext, ville: str = "") -> tuple[str, str]:
    """(nom_localité, type_ville) — la localité courante ou le paramètre."""
    nom = (ville or "").strip() or _lieu_nom(ctx)
    return nom, villes.type_de_ville(nom)


def _pc_long(pc: int) -> str:
    """« 25 pc », ou « 3 po — 5 pc » pour les gros montants (lisibilité)."""
    if pc < 20:
        return f"{pc} pc"
    po, reste = divmod(pc, 10)
    txt = f"{pc} pc"
    if po:
        txt += f" ({po} po" + (f", {reste} pc)" if reste else ")")
    return txt


def _ligne_article(art: dict[str, Any], type_ville: str) -> str:
    prix = phb.prix_article(art, type_ville)
    ligne = f"- **{art['nom']}** — {prix} pc ({art.get('prix_str')})"
    if art.get("categorie") == "service":
        ligne += " · service (aucun objet)"
    elif art.get("poids_kg"):
        ligne += f" · {art['poids_kg']} kg"
    vendeurs = phb.marchands_pour(art, type_ville)
    if vendeurs:
        ligne += f" · chez {' / '.join(vendeurs)}"
    return ligne


# --------------------------------------------------------------------------- #
#  Tools — catalogue
# --------------------------------------------------------------------------- #
@tool
async def equipement_catalogue(
    ctx: ToolContext, categorie: str = "", filtre: str = ""
) -> ToolResult:
    """
    Affiche le catalogue marchand officiel (PHB 3.5) : tous les articles
    vendables avec leur prix de BASE (non majoré par le marché local) et leur
    catégorie commerciale. Filtrage optionnel par `categorie` (arme, armure,
    bouclier, materiel, outil, substance, habillement, nourriture, boisson,
    monture, harnachement, transport, munition, service) ou par `filtre`
    (sous-chaîne du nom). Les PRIX RÉELS dépendent de la ville : consulte
    `marche_consulter`/`marche_stock` sur place.

    :param categorie (str): catégorie commerciale à filtrer (vide = tout).
    :param filtre (str): sous-chaîne du nom d'article (vide = tout).
    """
    lignes = [
        "📖 **Catalogue marchand — règles officielles (PHB 3.5)**",
        "_Prix de base (pc — 1 po = 10 pc). Le prix réel en ville varie : "
        "consulter `marche_consulter` / `marche_stock`._",
    ]
    n = 0
    for art in phb.articles():
        if categorie and art.get("categorie") != categorie:
            continue
        if filtre and _norm(filtre) not in _norm(art["nom"]):
            continue
        lignes.append(
            f"- **{art['nom']}** — {art['cout_pc']} pc ({art.get('prix_str')})"
            f" · {art.get('categorie')}"
            + (f" · {art.get('poids_kg')} kg" if art.get("poids_kg") else "")
        )
        n += 1
    lignes.append(f"\n**{n} article(s).** Monnaie : _1 po = 10 pc, "
                  "1 pa = 1 pc, 1 pièce de cuivre = 0,1 pc (min. 1 pc)._")
    return ToolResult(text="\n".join(lignes))


@tool
async def marche_consulter(
    ctx: ToolContext, ville: str = ""
) -> ToolResult:
    """
    Fait le tour du marché de la localité : type de peuplement (Hameau,
    Village, Bourg, Ville, Grande ville, Cité ou Métropole), coefficient de
    prix local, niveau maximum des sorts de service, qualité des auberges et
    liste des marchands présents avec ce qu'ils vendent. À appeler avant tout
    achat/vente pour savoir ce qui est disponible ICI.

    :param ville (str): nom de la localité (vide = celle où se trouve le
        groupe, d'après l'état de la partie).
    """
    nom, type_ville = _ville(ctx, ville)
    rng = villes.rang(type_ville)
    lignes = [
        f"🏘️ **Marché de {nom or 'la localité'}** "
        f"— {type_ville} (rang {rng}/6)",
        f"- Coefficient de prix : **×{villes.multiplicateur(type_ville)}**",
        f"- Sorts de service : jusqu'au **niveau "
        f"{villes.sorts_max_niveau(type_ville)}**",
        f"- Auberge : **{' / '.join(villes.auberge_qualites(type_ville))}** "
        "(qualités proposées)",
    ]
    m = villes.marchands(type_ville)
    lignes.append(f"- Marchands présents ({len(m)}) :")
    for key in m:
        meta = phb.MARCHANDS.get(key, {})
        lignes.append(f"  🛒 **{key}** — {meta.get('description', '')}")
    li = sorted(len(x) for x in m) or [0]
    lignes.append(
        f"\n💡 {len(m)} marchand(s) ici. Pour l'inventaire détaillé : "
        "`marche_stock(ville=…)` ; pour acheter/vendre un objet, précise son "
        "nom exact (`equipement_catalogue`).")
    return ToolResult(text="\n".join(lignes))


@tool
async def marche_stock(
    ctx: ToolContext, ville: str = "", marchand: str = "", categorie: str = ""
) -> ToolResult:
    """
    Inventaire exact de ce que les marchands de la localité vendent en ce
    moment, avec le prix effective (coefficient local inclus). Filtrage
    optionnel : `marchand` (« forge », « armurerie », « apothicaire », …) ou
    `categorie` (`arme`, `armure`, `substance`, `nourriture`, `monture`,
    « service », …). Les sortilèges loués apparaissent comme « Sort de
    service (niveau N) ».

    :param ville (str): localité (vide = celle du groupe).
    :param marchand (str): ne lister que les articles de ce marchand.
    :param categorie (str): ne lister que cette catégorie commerciale.
    """
    nom, type_ville = _ville(ctx, ville)
    m_req = phb._resoudre_marchand(marchand)
    lignes = [f"🏪 **Marché — {nom or 'localité'} ({type_ville})**"]
    n = 0
    for art in phb.articles():
        if categorie and art.get("categorie") != categorie:
            continue
        vendeurs = phb.marchands_pour(art, type_ville)
        if not vendeurs:
            continue
        if m_req and m_req not in vendeurs:
            continue
        lignes.append(_ligne_article(art, type_ville))
        n += 1
    # Sorts loués (services dynamiques).
    for niveau in range(1, villes.sorts_max_niveau(type_ville) + 1):
        art = phb.sorts_service(type_ville, niveau)
        if not art:
            continue
        vendeurs = phb.marchands_pour(art, type_ville)
        if vendeurs and (not m_req or m_req in vendeurs):
            lignes.append(_ligne_article(art, type_ville))
            n += 1
    lignes.append(f"\n**{n} article(s) vendu(s) ici**. "
                  "Achat : `marche_acheter` · vente : `marche_vendre`.")
    return ToolResult(text="\n".join(lignes))


# --------------------------------------------------------------------------- #
#  Achat / vente
# --------------------------------------------------------------------------- #
def _article_disponible(art: dict[str, Any], type_ville: str) -> list[str]:
    """Vendeurs; [] si l'article n'est pas vendu dans cette localité."""
    return phb.marchands_pour(art, type_ville)


def _article_ou_sort(ctx: ToolContext, nom: str, type_ville: str) -> tuple[Any, bool]:
    """Résout `nom` en article marchand ; renvoie aussi un éventuel sort de
    service dynamique (« Sort de service (niveau 2) »). -> (art|None, dyn)."""
    art = phb.article(nom)
    if art is not None:
        return art, False
    m = re.fullmatch(r"\s*(?:sort(?:il)?e? g? de service)?[ \t]*\(?niveau[ \t]+(\d+)\)?\s*",
                     nom, re.IGNORECASE)
    if m is None and "sort de service" in _norm(nom):
        m = re.search(r"niveau\s+(\d+)", nom, re.IGNORECASE)
    if m:
        niveau = int(m.group(1))
        art_s = phb.sorts_service(type_ville, niveau)
        if art_s is not None:
            return art_s, True
    return None, False


@tool
async def marche_acheter(
    ctx: ToolContext,
    nom: str,
    article: str,
    quantite: int = 1,
    ville: str = "",
) -> ToolResult:
    """
    Achète un article chez un marchand de la localité et l'enregistre dans
    l'inventaire PERMANENT du personnage (ration : reste au PJ). Montant
    débité du `or` (pc), poids ajouté au fardeau, coefficient local appliqué.
    Un article de catégorie « service » (sort loué, messager, écurie…) est
    réglé sans objet d'inventaire. Refusé si la localité ne vend pas
    l'article (cf. `marche_stock`) ou si le personnage n'a pas assez d'or.

    :param nom (str): personnage (ou joueur qui l'incarne).
    :param article (str): nom exact de l'article (`equipement_catalogue`).
    :param quantite (int): quantité d'unités à acheter (défaut 1).
    :param ville (str): localité (vide = celle du groupe).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    nom_ville, type_ville = _ville(ctx, ville)
    art, _dyn = _article_ou_sort(ctx, article, type_ville)
    if art is None:
        return ToolResult(
            text=f"❌ « {article} » inconnu du catalogue marchand — "
                 "liste complète : `equipement_catalogue`.")
    vendeurs = _article_disponible(art, type_ville)
    if not vendeurs:
        return ToolResult(
            text=f"❌ « {art['nom']} » n'est PAS vendu à "
                 f"{nom_ville or 'cette localité'} ({type_ville}) — "
                 "voir `marche_stock`.")
    qte = max(1, int(quantite or 1))
    prix_u = phb.prix_article(art, type_ville)
    total = prix_u * qte
    or_ = int(fiche.get("or", 0) or 0)
    if or_ < total:
        return ToolResult(
            text=f"❌ {fiche.get('nom', nom)} n'a que {or_} pc — {qte} × "
                 f"« {art['nom']} » coûte {total} pc "
                 f"({_ligne_desc(art, prix_u)}).")
    fiche["or"] = or_ - total

    lignes_ajout: list[str] = []
    if art.get("categorie") == "service":
        lignes_ajout.append(
            f"_(service réglé, aucun objet ajouté à l'inventaire)_")
    else:
        inv = _inventaire(fiche)
        cible = _cle_objet(art["nom"])
        fusionne = False
        for e in inv:
            if e.get("poids") is not None and _cle_objet(e.get("nom")) == cible:
                e["qte"] = int(e.get("qte", 1) or 1) + qte
                fusionne = True
                break
        if not fusionne:
            inv.append({
                "nom": art["nom"], "qte": qte,
                "poids": float(art.get("poids_kg") or 0),
            })
        fiche["inventaire"] = inv
        fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                               for i in inv if i.get("nom")]
        fiche = _normaliser_fiche(fiche)
        lignes_ajout.append(
            f"{qte} × **{art['nom']}** ajouté(s) à l'équipement permanent"
            + (f" ({round(float(art.get('poids_kg') or 0) * qte, 2)} kg)"
               if art.get("poids_kg") else ""))

    poids, cat, max_kg, patch, err_fin = _finaliser(ctx, nom, fiche)
    if err_fin:
        return err_fin
    ligne_or = f"- {fiche.get('nom', nom)} : **{or_} → {fiche['or']} pc** débités ({total} pc)"
    texte = (
        f"✅ **Achat chez {vendeurs[0]}** ({nom_ville or 'la localité'}, "
        f"type {type_ville}) :\n"
        + "\n".join(lignes_ajout)
        + f"\n{ligne_or} — {art['prix_str']} × {qte}"
    )
    if poids is not None:
        texte += "\n\n" + _format_inventaire(fiche, poids, cat, max_kg,
                                             partie_id=ctx.partie_id)
    return ToolResult(text=texte, state_patch=patch)


def _ligne_desc(art: dict[str, Any], prix_u: int) -> str:
    return f"({art['prix_str']} → {prix_u} pc avec le coin local)"


@tool
async def marche_vendre(
    ctx: ToolContext,
    nom: str,
    article: str,
    quantite: int = 1,
    ville: str = "",
) -> ToolResult:
    """
    Vend un objet de l'équipement permanent d'un personnage à un marchand de
    la localité. Revente à la MOITIÉ du prix de base (sans coefficient
    local), minimum 1 pc — règles officielles. Refusé si aucun marchand de la
    localité n'achète cette catégorie ou si le personnage n'a pas l'objet en
    quantité suffisante.

    :param nom (str): personnage (ou joueur qui l'incarne).
    :param article (str): nom de l'objet à vendre (ex. « Épée longue »).
    :param quantite (int): quantité à céder (défaut 1).
    :param ville (str): localité (vide = celle du groupe).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    nom_ville, type_ville = _ville(ctx, ville)
    art = phb.article(article)
    if art is None or art.get("categorie") == "service":
        return ToolResult(
            text=f"❌ « {article} » n'est pas un bien cessible (service ou "
                 "inconnu du catalogue marchand).")
    acheteurs = _article_disponible(art, type_ville)
    if not acheteurs:
        return ToolResult(
            text=f"❌ Aucun marchand à {nom_ville or 'cette localité'} "
                 f"({type_ville}) n'achète « {art['nom']} » (catégorie "
                 f"‘{art.get('categorie')}’).")
    inv = _inventaire(fiche)
    cible = _cle_objet(art["nom"])
    trouve = next(
        (e for e in inv
         if e.get("poids") is not None and _cle_objet(e.get("nom")) == cible),
        None,
    )
    if trouve is None:
        return ToolResult(
            text=f"❌ {fiche.get('nom', nom)} ne possède pas « {art['nom']} » "
                 "dans son équipement permanent.")
    qte = max(1, int(quantite or 1))
    dispo = int(trouve.get("qte", 1) or 1)
    if dispo < qte:
        return ToolResult(
            text=f"❌ {fiche.get('nom', nom)} n'a que {dispo} × "
                 f"« {art['nom']} » — impossible d'en vendre {qte}.")
    reste = dispo - qte
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)
    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    fiche = _normaliser_fiche(fiche)

    prix_u = phb.prix_revente(art)
    total = prix_u * qte
    fiche["or"] = int(fiche.get("or", 0) or 0) + total
    poids, cat, max_kg, patch, err_fin = _finaliser(ctx, nom, fiche)
    if err_fin:
        return err_fin
    texte = (
        f"💰 **Vente à {acheteurs[0]}** ({nom_ville or 'la localité'}) : "
        f"{qte} × **{art['nom']}** cédé(s) à {_pc_long(total)} — "
        f"{fiche.get('nom', nom)} a maintenant **{fiche['or']} pc** "
        f"({_pc_long(fiche['or'])}).\n"
        f"_(Revente à 50 % du prix de base : {art['prix_str']} → "
        f"{prix_u} pc/unité.)_"
    )
    if poids is not None:
        texte += "\n\n" + _format_inventaire(fiche, poids, cat, max_kg,
                                             partie_id=ctx.partie_id)
    return ToolResult(text=texte, state_patch=patch)


# --------------------------------------------------------------------------- #
#  Auberge
# --------------------------------------------------------------------------- #
_CONST_REAFFAMER = "Affamé"
_CONST_RASSASIE = "Rassasié"
_CONST_FATIGUE = "fatigue"
_CONST_EPUISE = "épuisé"


def _conditions(fiche: dict[str, Any]) -> list[str]:
    conds = fiche.get("conditions") or []
    if isinstance(conds, list):
        return list(conds)
    if isinstance(conds, str):
        return [c.strip() for c in conds.split(",") if c.strip()]
    return []


def _retirer_condition(fiche: dict[str, Any], nom_cond: str) -> bool:
    """Retire une condition (insensible à la casse). True si retirée."""
    conds = _conditions(fiche)
    avant = len(conds)
    conds = [c for c in conds if c.lower() != nom_cond.lower()]
    fiche["conditions"] = conds
    return len(conds) < avant


def _ajouter_condition(fiche: dict[str, Any], nom_cond: str) -> bool:
    conds = _conditions(fiche)
    if any(c.lower() == nom_cond.lower() for c in conds):
        return False
    conds.append(nom_cond)
    fiche["conditions"] = conds
    return True


@tool
async def auberge_commander(
    ctx: ToolContext,
    nom: str,
    repas: str = "",
    logement: str = "",
    nuits: int = 1,
    ville: str = "",
) -> ToolResult:
    """
    Passe commande à l'auberge de la localité : repas et/ou logement pour
    une ou plusieurs nuits, aux tarifs officiels (page 416). Qualités :
    « mediocre », « convenable » ou « bonne » — seules celles proposées par
    le type de localité sont servies (cf. `marche_consulter`).

    EFFETS (règles-maison du projet, documentés) :
      - repas (toute qualité) → retire la condition « Affamé » ;
      - repas de bonne qualité → ajoute la condition « Rassasié » ;
      - logement de bonne qualité → le PROCHAIN `repos_long` soigne
        +ceil(niveau/2) PV (≈ +50 %) et retire « fatigue »/« épuisé »
        (le marqueur est consommé au repos). Logement convenable/médiocre :
        repos normal.

    :param nom (str): personnage concerné (ou joueur qui l'incarne).
    :param repas (str): qualité du repas (« mediocre », « convenable »,
        « bonne ») ou vide pour ne pas en commander.
    :param logement (str): qualité du logement ou vide pour ne pas dormir.
    :param nuits (int): nombre de nuits de logement (défaut 1).
    :param ville (str): localité (vide = celle du groupe).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    nom_ville, type_ville = _ville(ctx, ville)
    qualites = villes.auberge_qualites(type_ville)

    def _cheque_qualite(q: str, label: str) -> Optional[str]:
        qn = _norm(q)
        if not qn or qn not in ("mediocre", "convenable", "bonne"):
            return f"Qualité « {q} » inconnue (mediocre | convenable | bonne)."
        if qn not in qualites:
            return (f"❌ {label} « {q} » indisponible à "
                    f"{nom_ville or 'cette localité'} ({type_ville}) — "
                    f"proposé : {qualites}.")
        return None

    repas_q = _norm(repas)
    log_q = _norm(logement)
    e1 = _cheque_qualite(repas_q, "Repas") if repas_q else None
    e2 = _cheque_qualite(log_q, "Logement") if log_q else None
    if e1:
        return ToolResult(text=e1)
    if e2:
        return ToolResult(text=e2)
    nuits_n = max(1, int(nuits or 1))

    tarifs = phb.auberge_prix  # → {"logement": pc/nuit, "repas": pc/jour}
    total = 0
    if repas_q:
        total += tarifs(repas_q)["repas"] * nuits_n
    if log_q:
        total += tarifs(log_q)["logement"] * nuits_n
    or_ = int(fiche.get("or", 0) or 0)
    if total > or_:
        return ToolResult(
            text=f"❌ {fiche.get('nom', nom)} n'a que {or_} pc — la note "
                 f"d'auberge s'élève à {total} pc ({_pc_long(total)})."
        )

    fiche["or"] = or_ - total
    effets: list[str] = []
    if repas_q:
        if _retirer_condition(fiche, _CONST_REAFFAMER):
            effets.append("condition « Affamé » retirée")
        if repas_q == "bonne" and _ajouter_condition(fiche, _CONST_RASSASIE):
            effets.append("condition « Rassasié » ajoutée")
    if log_q:
        if log_q == "bonne":
            fiche["auberge"] = {
                "logement": log_q, "qualite": log_q,
                "ville": nom_ville, "nuits": nuits_n,
                "date": datetime.now().isoformat(timespec="seconds"),
            }
            effets.append(
                "logement de bonne qualité → PROCHAIN repos_long : "
                "+ceil(niveau/2) PV et retrait de « fatigue »/« épuisé » "
                "(marqueur consommé)")

    poids, cat, max_kg, patch, err_fin = _finaliser(ctx, nom, fiche)
    if err_fin:
        return err_fin
    lignes = [
        f"🏨 **Auberge de {nom_ville or 'la localité'}** "
        f"({type_ville}) — {fiche.get('nom', nom)} :",
        f"- Repas : {'—' if not repas_q else repas_q}"
        + (f" ({tarifs(repas_q)['repas'] * nuits_n} pc, {nuits_n} jour(s))"
           if repas_q else ""),
        f"- Logement : {'—' if not log_q else log_q}"
        + (f" ({tarifs(log_q)['logement'] * nuits_n} pc, {nuits_n} nuit(s))"
           if log_q else ""),
        f"- Note totale : **{total} pc** — reste **{fiche['or']} pc**.",
    ]
    if effets:
        lignes.append("- Effets : " + " ; ".join(effets) + ".")
    lignes.append("_Tarifs officiels : repas 1/3/5 pa, logement 2/5 pa·2 po "
                  "(médiocre/convenable/bonne) — 1 po = 10 pc._")
    return ToolResult(text="\n".join(lignes), state_patch=patch)


def repos_bonus_auberge(fiche: dict[str, Any]) -> int:
    """Bonus PV du prochain repos long si nuitée « bonne » : +ceil(niveau/2)."""
    marqueur = fiche.get("auberge")
    if not isinstance(marqueur, dict) or marqueur.get("logement") != "bonne":
        return 0
    try:
        niveau = max(1, int(fiche.get("niveau") or 1))
    except (TypeError, ValueError):
        niveau = 1
    return (niveau + 1) // 2


def consommer_marqueur_auberge(fiche: dict[str, Any]) -> tuple[int, str]:
    """Lit et CONSOMME le marqueur d'auberge (nuitée bonne) : renvoie
    (bonus_pv, ville) — le marqueur est retiré de la fiche (une nuitée
    n'opère qu'AU PROCHAIN repos long)."""
    bonus = repos_bonus_auberge(fiche)
    marqueur = fiche.get("auberge")
    ville_nuit = ""
    if isinstance(marqueur, dict):
        ville_nuit = str(marqueur.get("ville") or "")
    if "auberge" in fiche:
        del fiche["auberge"]
    return bonus, ville_nuit