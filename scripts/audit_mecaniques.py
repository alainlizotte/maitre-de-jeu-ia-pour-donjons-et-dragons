# -*- coding: utf-8 -*-
"""Audit dynamique de TOUTES les mécaniques du jeu (outils exécutés réellement).

Chaque bloc exécute le(s) tool(s) de la mécanique sur un data_dir isolé et
imprime OK/KO + extrait. Les KO sont les pistes de correction.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool
from server.tools.registry import discover_tools

DATA = tempfile.mkdtemp(prefix="audit_mecanes_")
os.makedirs(os.path.join(DATA, "fiches"), exist_ok=True)
_REEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "server", "data")
# Bestiaire + scénarios réels : l'audit de combat/laelith en a besoin.
shutil.copy(os.path.join(_REEL, "bestiaire.json"),
            os.path.join(DATA, "bestiaire.json"))
shutil.copytree(os.path.join(_REEL, "scenarios", "Laelith"),
                os.path.join(DATA, "scenarios", "Laelith"),
                dirs_exist_ok=True)

CTX = ToolContext(partie_id="audit", joueur="Alain", data_dir=DATA)
TOOLS = discover_tools()


def fiche_ecrire(nom, fiche):
    with open(os.path.join(DATA, "fiches", f"fiche_{nom.lower()}.json"),
              "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=1)


def fiche_lire(nom):
    p = os.path.join(DATA, "fiches", f"fiche_{nom.lower()}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def etat_ecrire(etat):
    with open(os.path.join(DATA, "partie_audit.json"), "w",
              encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=1)


async def t(nom_tool, **args):
    try:
        r = await invoke_tool(TOOLS[nom_tool], CTX, args)
    except Exception as e:
        class _R:  # noqa
            text = f"EXCEPTION: {e}"
            ok = False
            state_patch = None
        return _R()
    # Applique le state_patch au fichier de partie (le flux production le
    # fait via l'orchestrateur) — sinon la chaîne de combat est invisible.
    patch = getattr(r, "state_patch", None)
    p = os.path.join(DATA, "partie_audit.json")
    if patch and os.path.exists(p):
        etat = json.load(open(p, encoding="utf-8"))
        for k, v in patch.items():
            if k.startswith("pj.0."):
                etat.setdefault("pj", [{}])[0][k.split(".", 2)[2]] = v
            else:
                etat[k] = v
        json.dump(etat, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return r


async def main() -> None:
    resultats: list[tuple[str, str, str]] = []

    def note(mec, ok, detail=""):
        resultats.append((mec, "OK" if ok else "KO", detail[:160]))

    # ── État de partie minimal + PJ ────────────────────────────────────
    etat_ecrire({
        "phase": "exploration",
        "lieu": {"nom": "Phandalin", "type": "Village"},
        "quete": {"titre": "Audit", "pitch": "Audit des mécaniques."},
        "pj": [{"nom": "Auditeur", "pv": 8, "pv_max": 8}],
        "memoire": {},
        "histoire": [],
    })
    fiche_ecrire("Auditeur", {
        "nom": "Auditeur", "race": "Humain", "classe": "Magicien",
        "niveau": 3, "xp": 900, "pv": 18, "pv_max": 18, "ca": 12,
        "bab": 1, "carac": {"FOR": 10, "DEX": 14, "CON": 12, "INT": 17,
                            "SAG": 11, "CHA": 10},
        "sauvegardes": {"Vigueur": 2, "Reflexes": 4, "Volonte": 5},
        "competences": {}, "dons": [], "equipement": [], "or": 2000,
        "inventaire": [], "conditions": [],
        "sorts": {"niveau0": ["Rayon de givre"], "niveau1": ["Sommeil"],
                  "niveau2": ["Invisibilité"]},
        "emplacements": {"niveau0": 4, "niveau1": 3, "niveau2": 2},
        "apparence": {"sexe": "F"},
    })

    # ── 1. DÉS ─────────────────────────────────────────────────────────
    r = await t("lancer_d20", nom_personnage="Auditeur",
                competence="Discrétion", difficulte=10, raison="audit")
    note("dés/d20+compétence", "1d20" in r.text or "Discrétion" in r.text,
         r.text[:100])
    r = await t("lancer_des", nb_des=3, faces=6, bonus=2, raison="audit dégâts")
    note("dés/lancer_des 3d6+2", "✅" in r.text or "3d6" in r.text
         or "total" in r.text.lower(), r.text[:100])
    r = await t("lancer_sauvegarde", nom_personnage="Auditeur",
                type_sauvegarde="Volonte", difficulte=12, raison="audit")
    note("dés/sauvegarde Vol DD12", "Volonté" in r.text or "Vol" in r.text,
         r.text[:100])

    # ── 2. CONDITIONS / PV ─────────────────────────────────────────────
    r = await t("fiche_perso_infliger_degats", nom="Auditeur", degats=7)
    f = fiche_lire("Auditeur")
    note("PV/infliger 7 → 11", f and f["pv"] == 11, f"pv={f and f['pv']}")
    r = await t("fiche_perso_soigner", nom="Auditeur", soin=50)
    f = fiche_lire("Auditeur")
    note("PV/soigner 50 (plafond pv_max)",
         f and f["pv"] == f["pv_max"], f"pv={f and f['pv']}")
    r = await t("fiche_perso_infliger_degats", nom="Auditeur", degats=30)
    f = fiche_lire("Auditeur")
    note("PV/infliger 30 → mort/KO",
         (f["pv"] <= 0) or any("mort" in str(c).lower()
                               for c in f.get("conditions", [])),
         f"pv={f['pv']} cond={f.get('conditions')}")
    r = await t("fiche_perso_soigner", nom="Auditeur", pv=30)
    f = fiche_lire("Auditeur")
    note("PV/soigner après mort (relève?)",
         f["pv"] > 0, f"pv={f['pv']} cond={f.get('conditions')}")

    # ── 3. SORTS ───────────────────────────────────────────────────────
    r = await t("preparer_sorts", nom_personnage="Auditeur",
                preparations_json=json.dumps({"Sommeil": 2}))
    note("sorts/préparation", "✅" in r.text or "prépar" in r.text.lower(),
         r.text[:100])
    r = await t("incanter_sort", nom_personnage="Auditeur",
                nom_sort="Sommeil", cible="Gobelin de test")
    note("sorts/incanter connu (Sommeil)",
         ("✅" in r.text or "incant" in r.text.lower())
         and "❌" not in r.text, r.text[:120])
    r = await t("incanter_sort", nom_personnage="Auditeur",
                nom_sort="Boule de feu", cible="X")   # niveau 3 > magicien 3? boule = niv3 → castable niv3? magicien 3 → slots niv3? testé tel quel
    note("sorts/incanter Boule de feu (niv 3)",
         "❌" in r.text or "✅" in r.text, r.text[:120])
    r = await t("incanter_sort", nom_personnage="Auditeur",
                nom_sort="Sort inventé", cible="X")
    note("sorts/inventé REFUSÉ", "❌" in r.text, r.text[:120])

    # ── 4. REPOS ───────────────────────────────────────────────────────
    r = await t("repos_long")
    note("repos_long (PV 1/niv = 3)", "✅" in r.text or "repos" in
         r.text.lower(), r.text[:120])

    # ── 5. XP / NIVEAUX ────────────────────────────────────────────────
    r = await t("fiche_perso_gagner_xp", nom="Auditeur", montant=1500)
    f = fiche_lire("Auditeur")
    note("XP/gagner 1500 (niveau recalculé)",
         f and f.get("xp", 0) >= 1500, f"xp={f and f.get('xp')} niv={f and f.get('niveau')}")
    r = await t("fiche_perso_perte_niveau", nom="Auditeur")
    f = fiche_lire("Auditeur")
    note("niveau/perte (niveau négatif)",
         f and (f.get("niveaux_negatifs", 0) >= 1
                or f.get("niveau", 3) < 3),
         f"niv={f and f.get('niveau')} neg={f and f.get('niveaux_negatifs')}")

    # ── 6. FAMILIER ────────────────────────────────────────────────────
    f = fiche_lire("Auditeur")
    f["or"] = 5000
    fiche_ecrire("Auditeur", f)
    r = await t("appeler_familier", nom_personnage="Auditeur")
    note("familier/appeler (100 po)",
         "✅" in r.text or "familier" in r.text.lower(), r.text[:120])

    # ── 7. MANUELS / RAG ───────────────────────────────────────────────
    r = await t("manuels_lister")
    note("manuels/lister", "manuel" in r.text.lower() or "📕" in r.text
         or "KB" in r.text, r.text[:100])

    # ── 8. CARTE DU MONDE ──────────────────────────────────────────────
    r = await t("carte_joueurs_placer_ville", ville="Phandalin")
    note("carte/position Phandalin", "✅" in r.text or "osition" in r.text,
         r.text[:100])
    r = await t("carte_joueurs_get")
    note("carte/get (SVG)", "svg" in r.text.lower() or "carte" in
         r.text.lower(), r.text[:100])

    # ── 9. MARCHÉ ──────────────────────────────────────────────────────
    r = await t("marche_stock")
    note("marché/stock local", "marchand" in r.text.lower() or "🛒" in
         r.text, r.text[:100])
    r = await t("marche_acheter", nom="Auditeur", article="Corde de chanvre (15 m)",
                quantite=1)
    f = fiche_lire("Auditeur")
    note("marché/acheter corde", "✅" in r.text, r.text[:100])
    r = await t("marche_vendre", nom="Auditeur", article="Corde de chanvre (15 m)",
                quantite=1)
    note("marché/vendre corde", "✅" in r.text or "pc" in r.text,
         r.text[:100])

    # ── 10. COMBAT COMPLET ─────────────────────────────────────────────
    r = await t("engager_combat", monstres="Gobelin, Gobelin")
    note("combat/engager 2 gobelins", "initiative" in r.text.lower()
         or "🎯" in r.text, r.text[:120])
    etat_lu = json.load(open(os.path.join(DATA, "partie_audit.json"),
                             encoding="utf-8"))
    mc = etat_lu.get("monstres_combat") or []
    note("combat/état monstres", len(mc) == 2,
         f"{[(m.get('nom'), m.get('pv')) for m in mc]}")
    r = await t("lancer_attaque", nom_attaquant="Auditeur",
                nom_cible="Gobelin", arme="Bâton")
    note("combat/attaque officielle", "touché" in r.text.lower()
         or "raté" in r.text.lower() or "🎲" in r.text, r.text[:140])
    etat_lu = json.load(open(os.path.join(DATA, "partie_audit.json"),
                             encoding="utf-8"))
    r = await t("tour_suivant_combat")
    note("combat/tour suivant", "tour" in r.text.lower() or "✅" in r.text,
         r.text[:120])
    r = await t("retraite_combat")
    note("combat/retraite", "✅" in r.text or "retraite" in r.text.lower()
         or "fuite" in r.text.lower(), r.text[:120])
    etat_lu = json.load(open(os.path.join(DATA, "partie_audit.json"),
                             encoding="utf-8"))
    note("combat/retraite sort du combat",
         not (etat_lu.get("monstres_combat")
              and etat_lu.get("phase") == "combat"),
         f"phase={etat_lu.get('phase')}")

    # ── 11. MÉMOIRE manuelle ───────────────────────────────────────────
    r = await t("memoire_intrigue", resume="Audit de l'intrigue.",
                objectif="Tester la mémoire.")
    etat_lu = json.load(open(os.path.join(DATA, "partie_audit.json"),
                             encoding="utf-8"))
    mem = etat_lu.get("memoire") or {}
    note("mémoire/intrigue manuelle",
         "Audit de l'intrigue" in str(mem.get("intrigue_resume") or ""),
         str(mem.get("intrigue_resume"))[:80])
    r = await t("memoire_lieu", nom="Phandalin", description="Village de départ.")
    etat_lu = json.load(open(os.path.join(DATA, "partie_audit.json"),
                             encoding="utf-8"))
    note("mémoire/lieu manuel",
         "Phandalin" in json.dumps(etat_lu.get("memoire") or {},
                                   ensure_ascii=False),
         json.dumps((etat_lu.get("memoire") or {}).get("lieux_visites") or [],
                    ensure_ascii=False)[:80])

    # ── 12. LAELITH ────────────────────────────────────────────────────
    r = await t("scenarios_laelith_lister")
    note("laelith/lister", "fontaine" in r.text.lower()
         or "laelith" in r.text.lower() or "📜" in r.text, r.text[:100])

    # ── 13. RESET ──────────────────────────────────────────────────────
    r = await t("reset_partie")
    note("reset_partie", "✅" in r.text or "reset" in r.text.lower(),
         r.text[:100])

    print("\n===== RÉSULTATS DE L'AUDIT =====")
    ko = 0
    for mec, st, det in resultats:
        marque = "✅" if st == "OK" else "❌"
        if st == "KO":
            ko += 1
        print(f"{marque} {mec:42} {det}")
    print(f"\n{len(resultats) - ko}/{len(resultats)} mécaniques OK, "
          f"{ko} KO")


asyncio.run(main())
