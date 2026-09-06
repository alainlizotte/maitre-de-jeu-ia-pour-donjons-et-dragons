"""Générateur de manifestes de donjon (`<nom>.donjon.json`) par scénario.

`carte_donjon_entrer` charge un plan CANONIQUE de donjon dès qu'un fichier
`*.donjon.json` (champ `scenario` = id de la quête) existe sous
`data/scenarios/` — la disposition, les descriptions et le contenu des
salles collent alors au module au lieu du procédural aléatoire.

Ce script produit un BROUILLON de manifeste pour un scénario donné, à
affiner à la main (ou avec l'aide du MJ) :

    py scripts/generer_donjon_scenario.py --scenario divers_dues_for_the_dead
    py scripts/generer_donjon_scenario.py --scenario laelith_x --salles 12
    py scripts/generer_donjon_scenario.py --lister

Démarche :
1. résout le scénario dans `scenarios_catalogue.json` (→ PDF) ;
2. extrait le texte du PDF (PyMuPDF) et détecte les créatures du bestiaire
   qui y sont mentionnées (résumé FR **ou EN**, alias inclus) ;
3. construit une disposition déterministe (graine = id du scénario) :
   enchaînement linéaire de salles + embranchements, boss = créature de
   plus haut FP dans la salle finale, 1-2 trésors, 1-2 pièges, escaliers ;
4. écrit `<stem du pdf>.donjon.json` à côté du PDF — ne réécrase JAMAIS un
   manifeste existant sans `--force`.

Le brouillon est volontairement simple : le vrai travail d'authoring
(descriptions fidèles au module, placement exact des rencontres) se fait
ensuite en éditant le JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

_RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RACINE))

from server.tools.scenarios import ennemis_du_resume  # noqa: E402

# Types de salles (alignés sur TYPES_SALLES de server/tools/cartes.py).
_TYPES = [
    "antichambre", "couloir", "salle vide", "garde", "crypte", "trésor",
    "piège", "autel", "cellules", "puits", "abattoir", "bibliothèque",
    "entrée", "laboratoire", "escaliers",
]


def _data_dir() -> Path:
    from server.config import get_config
    return get_config().abs(get_config().paths.data_dir)


def _cr_numerique(fp: Any) -> float | None:
    txt = str(fp or "").strip().lower().replace(",", ".")
    if not txt:
        return None
    try:
        if "/" in txt:
            num, den = txt.split("/", 1)
            return float(num) / float(den)
        return float(txt)
    except (ValueError, ZeroDivisionError):
        return None


def lister_scenarios() -> list[dict[str, Any]]:
    """Aplati le catalogue : [{id, titre, univers, pdf_path}, …]."""
    cata_path = _data_dir() / "scenarios_catalogue.json"
    if not cata_path.is_file():
        return []
    cata = json.loads(cata_path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for u in cata.get("universes", []) or []:
        for s in u.get("scenarios", []) or []:
            out.append({
                "id": str(s.get("id") or ""),
                "titre": str(s.get("titre") or ""),
                "univers": str(u.get("nom") or ""),
                "pdf": str(s.get("pdf") or ""),
            })
    return out


def _resoudre_pdf(sid: str) -> Path | None:
    for s in lister_scenarios():
        if s["id"] == sid and s["pdf"]:
            rel = s["pdf"].split("/data/", 1)[-1]
            p = _data_dir() / rel
            if p.is_file():
                return p
    return None


def _extraire_texte(pdf: Path, plafond: int = 60000) -> str:
    import pymupdf  # type: ignore[import-untyped]
    with pymupdf.open(str(pdf)) as doc:
        texte = "\n".join(page.get_text() for page in doc)
    return texte[:plafond]


def _ennemis_avec_fp(best: dict[str, Any], texte: str) -> list[dict[str, Any]]:
    """Créatures du bestiaire détectées dans le texte, triées par FP croissant."""
    noms = ennemis_du_resume(texte, best)
    par_nom = {
        str((m or {}).get("nom") or cle): m
        for cle, m in (best.get("monstres") or {}).items()
        if isinstance(m, dict)
    }
    out: list[dict[str, Any]] = []
    for nom in noms:
        m = par_nom.get(nom) or {}
        out.append({
            "nom": nom,
            "fp": str(m.get("fp") or "?"),
            "cr": _cr_numerique(m.get("fp")) or 0.0,
        })
    out.sort(key=lambda e: e["cr"])
    return out


def construire_manifeste(
    sid: str,
    texte: str,
    best: dict[str, Any],
    donjon_id: str = "",
    nb_salles: int = 10,
    graine: str = "",
) -> dict[str, Any]:
    """Construit un brouillon de manifeste (schéma `*.donjon.json`).

    Disposition déterministe (graine = graine ou sid) : chaîne principale de
    salles en serpentin + éventuel embranchement ; les créatures détectées
    sont réparties du plus faible (proche de l'entrée) au plus fort (salle
    finale = boss). Les descriptions restent à remplir (chaîne vide).
    """
    rng = random.Random(graine or sid)
    tous_ennemis = _ennemis_avec_fp(best, texte)
    # Créatures jouables par un groupe solo niveau 1 (plafond FP 5, même
    # logique que la garde de `engager_combat`) : au-delà, la garde refuserait
    # l'engagement — la créature passe alors en `note` pour ajustement manuel
    # (ajouter le nombre, dégrader le monstre ou reprogrammer la rencontre).
    _FP_PLAFOND_BROUILLON = 5.0
    ennemis = [e for e in tous_ennemis if e["cr"] <= _FP_PLAFOND_BROUILLON]
    trop_forts = [e for e in tous_ennemis if e["cr"] > _FP_PLAFOND_BROUILLON]
    nb = max(6, min(int(nb_salles), 20))
    etage: list[dict[str, Any]] = []
    x, y = 0, 0
    occupees: set[tuple[int, int]] = {(x, y)}
    for i in range(nb):
        if i == 0:
            typ = "entrée"
        elif i == nb - 1:
            typ = "salle du trône"
        elif i == nb - 2 and rng.random() < 0.5:
            typ = "escaliers"
        else:
            typ = rng.choice(_TYPES)
        salle: dict[str, Any] = {
            "x": x, "y": y, "type": typ,
            "portes": {"nord": False, "sud": False, "est": False, "ouest": False},
            "description": "",
        }
        etage.append(salle)
        if i >= nb - 1:
            break
        # Prochaine case : adjacente LIBRE (pas de collision — sinon une
        # salle écrasait une autre et restait inaccessible), progression
        # horizontale privilégiée, descentes occasionnelles au nord.
        options: list[tuple[str, int, int]] = []
        for d_nom, dx, dy in (
            ("est", 1, 0), ("ouest", -1, 0), ("nord", 0, -1), ("sud", 0, 1),
        ):
            if (x + dx, y + dy) not in occupees and abs(x + dx) <= 8 \
                    and abs(y + dy) <= 8:
                options.append((d_nom, x + dx, y + dy))
        if not options:  # impasse (garde-fou, quasi impossible sur ±8)
            break
        ponderee = [
            o for o in options
            for _ in range(2 if o[0] in ("est", "ouest") else 1)
        ]
        d_choisi, nx2, ny2 = rng.choice(ponderee)
        salle["portes"][d_choisi] = True
        x, y = nx2, ny2
        occupees.add((x, y))
    # Portes de retour (topologie cohérente : la salle suivante ouvre vers la
    # précédente — `carte_donjon_explorer` ajoute déjà la porte de retour,
    # mais on la déclare pour la lisibilité du JSON).
    idx_par_cle = {(s["x"], s["y"]): i for i, s in enumerate(etage)}
    _opp = {"nord": "sud", "sud": "nord", "est": "ouest", "ouest": "est"}
    for i, s in enumerate(etage):
        for d, ouvert in (s.get("portes") or {}).items():
            if not ouvert:
                continue
            dx, dy = {
                "nord": (0, -1), "sud": (0, 1), "est": (1, 0), "ouest": (-1, 0),
            }[d]
            voisin = idx_par_cle.get((s["x"] + dx, s["y"] + dy))
            if voisin is not None:
                etage[voisin]["portes"][_opp[d]] = True

    # Contenu : boss (FP max jouable) en salle finale, gardes avant,
    # trésors/pièges. Les créatures trop fortes (FP > 5) sont signalées en
    # note de la salle finale pour ajustement manuel.
    if etage:
        if ennemis:
            boss = ennemis[-1]
            etage[-1]["ennemis"] = [f"{boss['nom']} ×1"]
            etage[-1]["description"] = (
                etage[-1].get("description")
                or f"[À RÉDIGER — antre du « {boss['nom']} », final du scénario « {sid} ». "
                "Reprendre la description du module.]"
            )
            gardes = list(reversed(ennemis[:-1]))
            milieu = list(range(2, len(etage) - 2)) or [1]
            for j, e in enumerate(gardes):
                i_salle = milieu[j % len(milieu)]
                etage[i_salle].setdefault("ennemis", []).append(f"{e['nom']} ×1")
        if trop_forts:
            etage[-1]["note"] = (
                "[ADJUSTEMENT REQUIS] Créatures du module trop puissantes "
                "pour un groupe niveau 1 (FP > 5, refusées par la garde de "
                f"combat) : "
                + ", ".join(f"{e['nom']} (FP {e['fp']})" for e in trop_forts)
                + ". Replacer manuellement une version dégradée, un groupe "
                "de créatures plus faibles, ou réserver à un groupe de "
                "niveau supérieur."
            )
    nb_tresors = 1 if nb < 10 else 2
    for i_t in rng.sample(range(1, max(2, nb - 2)), nb_tresors):
        s = etage[i_t]
        if not s.get("ennemis"):
            s["type"] = "trésor"
            s["tresor"] = "[À RÉDIGER — trésor du module]"
    nb_pieges = 1 if nb < 10 else 2
    places = 0
    for i_p in rng.sample(range(1, max(2, nb - 2)), nb_pieges * 2):
        if places >= nb_pieges:
            break
        s = etage[i_p]
        if s.get("piege") or s.get("ennemis") or s["type"] in ("entrée", "trésor", "escaliers", "salle du trône"):
            continue
        s["type"] = "piège"
        s["piege"] = "[À RÉDIGER — piège du module (DD, dégâts, effets)]"
        places += 1

    return {
        "id": f"{sid}_donjon",
        "scenario": sid,
        "donjon_id": donjon_id or sid,
        "titre": f"Donjon du scénario {sid} (BROUILLON)",
        "source": f"[BROUILLON GÉNÉRÉ — affiner à partir du module : {pdf_note()}]",
        "note_convention": (
            "Brouillon auto-généré : disposition déterministe, créatures "
            "détectées automatiquement dans le texte du module (résumé FR/EN). "
            "À affiner : descriptions fidèles, placement exact des "
            "rencontres/trésors/pièges, PNJ, second étage si nécessaire."
        ),
        "etages": [
            {
                "nom": "Rez-de-chaussée",
                "entree": [0, 0],
                "salles": etage,
            }
        ],
    }


def pdf_note() -> str:  # pragma: no cover - helper cosmétique
    return "voir le PDF du scénario dans server/data/scenarios/"


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Génère un brouillon de manifeste de donjon pour un scénario.")
    parseur.add_argument("--scenario", help="id du scénario (ex. divers_dues_for_the_dead)")
    parseur.add_argument("--lister", action="store_true", help="liste les scénarios et sort")
    parseur.add_argument("--salles", type=int, default=10, help="nombre de salles (6-20)")
    parseur.add_argument("--donjon-id", default="", help="nom du donjon (défaut : titre du scénario)")
    parseur.add_argument("--force", action="store_true", help="réécrase un manifeste existant")
    args = parseur.parse_args()

    if args.lister:
        for s in lister_scenarios():
            print(f"{s['id']:40} | {s['univers']:20} | {s['titre']}")
        return 0
    if not args.scenario:
        parseur.error("--scenario requis (ou --lister)")

    pdf = _resoudre_pdf(args.scenario)
    if pdf is None:
        print(f"❌ Scénario « {args.scenario} » introuvable ou sans PDF.")
        return 1
    dest = pdf.with_suffix("").with_name(pdf.stem + ".donjon.json")
    if dest.is_file() and not args.force:
        print(f"ℹ️ Manifeste déjà présent : {dest.name} — utilisez --force pour le réécraser.")
        return 0

    from server.tools.monstres import _load_bestiaire
    from server.tools.base import ToolContext
    ctx = ToolContext(partie_id="gen_donjon", joueur="script", data_dir=str(_data_dir()))
    best = _load_bestiaire(ctx)
    texte = _extraire_texte(pdf)
    man = construire_manifeste(
        args.scenario, texte, best,
        donjon_id=args.donjon_id,
        nb_salles=args.salles,
    )
    dest.write_text(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ennemis = _ennemis_avec_fp(best, texte)
    print(f"✅ Brouillon écrit : {dest}")
    print(f"   Salles : {len(man['etages'][0]['salles'])} | "
          f"Créatures détectées : {', '.join(e['nom'] for e in ennemis) or 'aucune'}")
    print("   → À FAIRE : rédiger les descriptions fidèles au module, ajuster")
    print("     le placement des rencontres/trésors/pièges, nommer les PNJ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
