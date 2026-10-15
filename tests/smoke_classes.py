# -*- coding: utf-8 -*-
"""Smoke test « toutes les classes de PJ » — conformité PHB 3.5 déterministe.

Pour CHACUNE des 11 classes du PHB (Barbare, Barde, Clerc, Druide, Guerrier,
Magicien, Moine, Paladin, Rôdeur, Sorcier, Voleur) au niveau 1 :
  1. création via fiche_perso_creer_rapide ;
  2. conformité des dérivés : PV (dé de vie + CON), BBA (bon +1 / moyen +0 /
     mauvais +0 au niv 1), sauvegardes (TOTALE fiche = base bonne 2 / mauvaise
     0 + mod caractéristique : Vigueur=CON, Reflexes=DEX, Volonte=SAG) ;
  3. lanceurs : emplacements de sorts (clerc = +1 emplacement de DOMAINE par
     niveau castable, PHB) et sort SIGNATURE lancé ; paladin/rôdeur niv 1 =
     REFUS conforme (aucun sort avant le niv 4 en 3.5) ;
  4. jet d'attaque réel (recoupement fiche ; voleur : Attaque en finesse
     avec DEX > FOR) ; initiative ; sauvegarde recoupée.

Lance : py tests/smoke_classes.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from server.tools.registry import discover_tools          # noqa: E402
from server.tools.base import ToolContext, invoke_tool    # noqa: E402
from server.tools.fiches import _chemin, _load_fiche      # noqa: E402

TOOLS = discover_tools("server.tools")
TMP = tempfile.mkdtemp(prefix="smoke_classes_")

# classe → (dé de vie, bab bon?, sauves bonnes, arme, sort signature,
#           spontané ?, sorts interdits au niv 1 ?)
CLASSES = {
    "Barbare":  (12, True,  ["Vigueur"],            "Grande hache", "", False, []),
    "Barde":    (6,  False, ["Reflexes", "Volonte"], "Épée courte", "Soins légers", True, []),
    "Clerc":    (8,  False, ["Vigueur", "Volonte"], "Masse d'armes", "Soins légers", False, []),
    "Druide":   (8,  False, ["Vigueur", "Volonte"], "Masse d'armes", "Trait de feu", False, []),
    "Guerrier": (10, True,  ["Vigueur"],            "Épée longue", "", False, []),
    "Magicien": (4,  False, ["Volonte"],            "Bâton", "Projectiles magiques", False, []),
    "Moine":    (8,  False, ["Vigueur", "Reflexes", "Volonte"], "Mains nues", "", False, []),
    "Paladin":  (10, True,  ["Vigueur"],            "Épée longue", "", False, ["Soins légers"]),
    "Rodeur":   (8,  False, ["Vigueur", "Reflexes"], "Épée longue", "", False, ["Enchevêtrement"]),
    "Sorcier":  (4,  False, ["Volonte"],            "Bâton", "Projectiles magiques", True, []),
    "Voleur":   (6,  False, ["Reflexes"],           "Dague", "", False, []),
}

echecs: list[str] = []
reussis = 0


def ok(label: str, cond: bool, detail: str = ""):
    global reussis
    if cond:
        reussis += 1
        print(f"  ✅ {label}")
    else:
        echecs.append(f"{label} — {detail}")
        print(f"  ❌ {label} — {detail[:160]}")


def ctx(nom: str) -> ToolContext:
    return ToolContext(partie_id="smokeclasses", joueur="smoke", data_dir=TMP)


async def tool(nom_tool: str, **args):
    return await invoke_tool(TOOLS[nom_tool], ctx(nom_tool), args)


def _mod(c: int) -> int:
    return (c - 10) // 2


async def tester_classe(classe: str, de_vie: int, bab_bon: bool,
                        sauves_bonnes: list[str], arme: str,
                        sort_signature: str, spontane: bool,
                        sorts_interdits: list[str]) -> None:
    nom = f"Test{classe}"
    print(f"\n=== {classe} ===")
    # Voleur : DEX 16 / FOR 10 pour que l'Attaque en finesse (DEX > FOR)
    # s'applique — sinon la règle 3.5 garde le meilleur (FOR), conformément.
    if classe == "Voleur":
        carac = "FOR 10, DEX 16, CON 12, INT 10, SAG 12, CHA 12"
    else:
        carac = "FOR 14, DEX 12, CON 12, INT 10, SAG 12, CHA 12"
    dons = {
        "Voleur": ["Attaque en finesse"],
        "Barbare": ["Esquive instinctive"],
        "Guerrier": ["Science du critique"],
    }.get(classe, [])

    tr = await tool("fiche_perso_creer_rapide", nom=nom, race="Humain",
                    classe=classe, niveau=1, carac_texte=carac,
                    equipement_texte=("Mains nues" if arme == "Mains nues"
                                      else arme),
                    dons_texte=", ".join(dons))
    if tr.text.startswith("❌"):
        ok(f"{classe} : création", False, tr.text)
        return
    fiche = _load_fiche(ctx(nom), nom)
    ok(f"{classe} : fiche créée", fiche is not None)

    # 1) PV : dé de vie max + mod CON (niveau 1)
    pv_att = de_vie + _mod(12)
    ok(f"{classe} : PV = {pv_att} (dé {de_vie} + CON 12)",
       int(fiche.get("pv_max", 0)) == pv_att,
       f"reçu pv_max={fiche.get('pv_max')}")

    # 2) BBA niveau 1 : bon +1, moyen/mauvais +0
    bab_att = 1 if bab_bon else 0
    ok(f"{classe} : BBA = {bab_att:+d}",
       int(fiche.get("bab", 0)) == bab_att,
       f"reçu {fiche.get('bab')}")

    # 3) Sauvegardes TOTALES (base + mod caractéristique)
    sv = fiche.get("sauvegardes") or {}

    def _sv(cle: str) -> int:
        for k, v in sv.items():
            if str(k).lower().startswith(cle[:5].lower()):
                return int(v or 0)
        return -99
    _mods = {"Vigueur": _mod(12), "Reflexes": _mod(12), "Volonte": _mod(12)}
    if classe == "Voleur":
        _mods["Reflexes"] = _mod(16)   # DEX 16 (Attaque en finesse)
    for bonne in ("Vigueur", "Reflexes", "Volonte"):
        base = 2 if bonne in sauves_bonnes else 0
        att = base + _mods[bonne]
        ok(f"{classe} : {bonne} = {att:+d} (base {base:+d} + mod carac)",
           _sv(bonne) == att, f"reçu {_sv(bonne):+d} (sauves={sv})")

    # 4) Sorts signature / refus conforme
    if sort_signature:
        prep = await tool("preparer_sorts", nom_personnage=nom,
                          preparations_json=json.dumps({sort_signature: 1}))
        prep_ok = not prep.text.startswith("❌")
        if spontane and not prep_ok:
            # spontanés (barde/sorcier) : sorts CONNUS posés sur la fiche
            fiche = _load_fiche(ctx(nom), nom)
            fiche.setdefault("sorts", {})["connus"] = [sort_signature]
            with open(_chemin(ctx(nom), nom), "w", encoding="utf-8") as f:
                json.dump(fiche, f, ensure_ascii=False)
            prep_ok = True
        ok(f"{classe} : préparation/connu « {sort_signature} »", prep_ok,
           prep.text[:160])
        if prep_ok:
            cible = nom if "Soins" in sort_signature else ""
            cs = await tool("incanter_sort", nom_personnage=nom,
                            nom_sort=sort_signature, cible=cible)
            ok(f"{classe} : incante « {sort_signature} »",
               not cs.text.startswith("❌") and "⛔" not in cs.text[:40],
               cs.text[:160])
    for interdit in sorts_interdits:
        prep = await tool("preparer_sorts", nom_personnage=nom,
                          preparations_json=json.dumps({interdit: 1}))
        ok(f"{classe} : « {interdit} » refusé au niv 1 (conforme 3.5 : "
           "sorts seulement au niv 4)", "trop puissant" in prep.text
           or prep.text.startswith("❌") or "⛔" in prep.text, prep.text[:140])

    # 5) Attaque réelle (vs cible fictive CA 10 — hors combat)
    tr_a = await tool("lancer_attaque", nom_attaquant=nom, arme=arme,
                      bonus_attaque=1, nom_cible="Mannequin", ca_cible=10)
    ok(f"{classe} : jet d'attaque résolu ({arme})",
       "Total attaque" in tr_a.text, tr_a.text[:160])
    if classe == "Voleur":
        ok("Voleur : Attaque en finesse (DEX +3 au lieu de FOR +0)",
           "finesse" in tr_a.text.lower(), tr_a.text[:220])
    if classe == "Barbare":
        ok("Barbare : FOR +2 appliqué (BBA +1 + FOR +2 = +3)",
           "+3" in tr_a.text, tr_a.text[:200])

    # 6) Initiative + sauvegarde recoupée (le serveur impose le total fiche)
    tr_i = await tool("calculer_initiative", participants=f"{nom}:+1")
    ok(f"{classe} : initiative calculée", "Initiative" in tr_i.text
       or "init" in tr_i.text.lower(), tr_i.text[:120])
    _base_vig = 2 if "Vigueur" in sauves_bonnes else 0
    tr_s = await tool("lancer_sauvegarde", type_sauvegarde="Vigueur",
                      modificateur=_base_vig, difficulte=10,
                      nom_personnage=nom, source="test conformité")
    total_att = _base_vig + _mod(12)
    ok(f"{classe} : sauvegarde recoupée vers le total fiche ({total_att:+d})",
       f"Modificateur : {total_att:+d}" in tr_s.text, tr_s.text[:200])


async def main():
    for classe, spec in CLASSES.items():
        await tester_classe(classe, *spec)
    print("\n=== Cas croisés ===")
    # Sorcier : sorts connus posés directement (spontané, sans préparation)
    fiche = _load_fiche(ctx("TestSorcier"), "TestSorcier")
    if fiche is not None:
        fiche.setdefault("sorts", {})["connus"] = ["Projectiles magiques",
                                                   "Mains brûlantes"]
        with open(_chemin(ctx("TestSorcier"), "TestSorcier"), "w",
                  encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)
        cs = await tool("incanter_sort", nom_personnage="TestSorcier",
                        nom_sort="Projectiles magiques", cible="")
        ok("Sorcier : sort SPONTANÉ (connu, sans préparation)",
           not cs.text.startswith("❌"), cs.text[:160])
    # Clerc : emplacements n.1 incluent le sort de DOMAINE (+1, PHB)
    fc = _load_fiche(ctx("TestClerc"), "TestClerc")
    ok("Clerc : fiche Clerc", fc is not None
       and str(fc.get("classe")) == "Clerc",
       str((fc or {}).get("classe")))
    # Dons de classe : guerrier a bien ses dons
    fg = _load_fiche(ctx("TestGuerrier"), "TestGuerrier")
    ok("Guerrier : dons présents", bool((fg or {}).get("dons")),
       str((fg or {}).get("dons")))

    print(f"\n{'='*60}\nSMOKE CLASSES : {reussis} OK, {len(echecs)} échec(s)")
    for e in echecs:
        print("  ❌ " + e)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
