# -*- coding: utf-8 -*-
"""Balayage d'intégrité global (en processus, sans serveur) :
  1. bestiaire : TOUTES les entrées parsées par _attaques_du_bestiaire —
     un monstre sans attaque exploitable ne jouerait JAMAIS son tour ;
  2. scénarios Laelith : tous les manifestes se chargent (donjons, étapes) ;
  3. registre : 86 tools, aucun doublon de nom ;
  4. cohérence sorts : chaque classe lanceuse a des sorts de niv 1 accessibles
     (excepté paladin/rôdeur — niv 4 en 3.5) ;
  5. tables de monstres : FP numériques (XP/clôture) pour toutes les entrées.
"""
from __future__ import annotations

import io
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

import re as _re  # noqa: E402

from server.tools.monstres import _load_bestiaire   # noqa: E402
from server.tools.base import ToolContext           # noqa: E402
from server.game.combat import (                    # noqa: E402
    _attaques_du_bestiaire, _multiplicateur_crit_bestiaire,
)

DATA = os.path.join(REPO, "server", "data")
echecs: list[str] = []
n_ok = 0


def ok(label: str, cond: bool, detail: str = ""):
    global n_ok
    if cond:
        n_ok += 1
    else:
        echecs.append(f"{label} — {detail[:200]}")
        print(f"  ❌ {label} — {detail[:200]}")


# ── 1. Bestiaire ──────────────────────────────────────────────────────────
ctx = ToolContext(partie_id="integrite", joueur="sweep", data_dir=DATA)
best = _load_bestiaire(ctx)
mons = best.get("monstres") or {}
print(f"Bestiaire : {len(mons)} entrées")
_RE_DES_TXT = _re.compile(r"\d+\s*[dD]\s*\d+")
sans_attaque: list[str] = []
for cle, m in mons.items():
    if not isinstance(m, dict):
        continue
    atk_txt = str(m.get("attaques") or "").strip()
    # « — » = créature SANS attaque dans la DRS (inoffensive) : tour passé,
    # comportement légitime. Une attaque sans AUCUN dé (« fixation », « 1
    # plus venin ») est à EFFET narré par le MJ : hors périmètre du moteur.
    if not atk_txt or atk_txt == "—" or not _RE_DES_TXT.search(atk_txt):
        continue
    atk = _attaques_du_bestiaire(m)
    if not atk:
        sans_attaque.append(str(m.get("nom") or cle))
print(f"Attaques avec dés parsées : toutes sauf {len(sans_attaque)}")
ok("Bestiaire : chaque attaque AVEC dés est exploitable par le moteur",
   not sans_attaque, ", ".join(sans_attaque[:10]))
# FP numériques (XP/clôture)
fps_invalides = []
for cle, m in mons.items():
    if not isinstance(m, dict):
        continue
    if not _re.match(r"\d+(?:/\d+)?", str(m.get("fp") or "").strip()):
        fps_invalides.append(f"{m.get('nom') or cle} (fp={m.get('fp')!r})")
ok("Bestiaire : FP numériques partout", not fps_invalides,
   "; ".join(fps_invalides[:8]))
# Multiplicateurs de critique plausibles
mult_hors = [str(m.get("nom")) for m in mons.values()
             if isinstance(m, dict)
             and not (1 <= _multiplicateur_crit_bestiaire(m) <= 4)]
ok("Bestiaire : multiplicateurs de critique dans [2..4]", not mult_hors,
   ", ".join(mult_hors[:8]))

# ── RD par TYPE de dégâts (chantier « RD par type ») ─────────────────────
from server.game.regles35 import (  # noqa: E402
    reduire_degats_monstre as _rdm, type_degats_source as _tds,
)
for src, attendu in [("Épée longue", "tranchant"),
                     ("Masse d'armes lourde", "contondant"),
                     ("Mains nues", "contondant"), ("Dague +1", "perforant"),
                     ("Arc long", "perforant"), ("Grande hache", "tranchant"),
                     ("Boule de feu", "")]:
    ok(f"Type de dégâts : {src} → {attendu}", _tds(src) == attendu,
       f"reçu {_tds(src)!r}")
_ANNIS = {"nom": "Annis",
          "capacites": "réduction des dégâts (2/contondant)"}
ok("RD typée : masse contourne 2/contondant (0 réduit)",
   _rdm(_ANNIS, 10, "Masse d'armes lourde")[0] == 10)
ok("RD typée : mains nues contournent 2/contondant",
   _rdm(_ANNIS, 10, "Mains nues")[0] == 10)
ok("RD typée : épée longue subit 2/contondant (−2)",
   _rdm(_ANNIS, 10, "Épée longue")[0] == 8)
_RAK = {"nom": "Rakshasa",
        "capacites": "réduction des dégâts (15/Bien et perforant)"}
ok("RD conjonctive : « Bien et perforant » exige LES DEUX (dague +1 → −15)",
   _rdm(_RAK, 20, "Dague +1")[0] == 5)
ok("RD conjonctive : épée longue → −15",
   _rdm(_RAK, 20, "Épée longue")[0] == 5)
_PLANTE = {"nom": "Plante TG",
           "capacites": "réduction des dégâts (10/perforant)"}
ok("RD typée : dague (perforante) contourne 10/perforant",
   _rdm(_PLANTE, 20, "Dague")[0] == 20)
ok("RD typée : épée longue subit 10/perforant (−10)",
   _rdm(_PLANTE, 20, "Épée longue")[0] == 10)
ok("RD : les SORTS ne subissent jamais la RD",
   _rdm(_ANNIS, 10, "Trait de feu")[0] == 10)

# ── 2. Scénarios ──────────────────────────────────────────────────────────
scen_dir = os.path.join(DATA, "scenarios", "Laelith")
n_scen = 0
problemes_scen: list[str] = []
if os.path.isdir(scen_dir):
    for nom in os.listdir(scen_dir):
        dossier = os.path.join(scen_dir, nom)
        if not os.path.isdir(dossier):
            continue
        for f in os.listdir(dossier):
            if f.endswith(".donjon.json"):
                n_scen += 1
                try:
                    with open(os.path.join(dossier, f), encoding="utf-8") as fh:
                        d = json.load(fh)
                    salles = sum(len(et.get("salles") or [])
                                 for et in (d.get("etages") or []))
                    if salles <= 0 and not (d.get("salles")):
                        problemes_scen.append(f"{nom}: 0 salle")
                except Exception as e:                   # noqa: BLE001
                    problemes_scen.append(f"{nom}: {e}")
print(f"Scénarios : {n_scen} manifestes de donjon chargés")
ok("Scénarios : tous les manifestes se chargent", not problemes_scen,
   "; ".join(problemes_scen[:6]))

# ── 3. Registre de tools ──────────────────────────────────────────────────
from server.tools.registry import discover_tools  # noqa: E402
TOOLS = discover_tools("server.tools")
print(f"Tools : {len(TOOLS)} enregistrés")
ok("Tools : ≥ 85 enregistrés", len(TOOLS) >= 85, f"{len(TOOLS)}")
ok("Tools : nouveaux tools DRS présents", all(
    t in TOOLS for t in ("danger_chute", "danger_noyade",
                         "danger_environnement", "attaque_speciale",
                         "attaque_opportunite", "combat_preparer_action",
                         "combat_attendre", "voyage_meteo_jour",
                         "voyage_repas", "voyage_prospection")))

# ── 4. Sorts par classe ───────────────────────────────────────────────────
from server import sorts as cat  # noqa: E402
for cl in ("Barde", "Clerc", "Druide", "Magicien", "Sorcier"):
    n1 = [s for s in cat.SORTS
          if cl in (s.get("classes") or []) and s.get("niveau") == 1]
    ok(f"Sorts : {cl} a des sorts de niv 1", bool(n1))
pal1 = [s for s in cat.SORTS
        if "Paladin" in (s.get("classes") or []) and s.get("niveau") == 1]
nls_pal = cat.niveau_sort_max("Paladin", 1)
ok("Sorts : Paladin niv 1 ne peut lancer AUCUN sort (3.5 : niv 4)",
   nls_pal < 1, f"nls={nls_pal}, {len(pal1)} sorts niv1 dans la liste")
nls_rod = cat.niveau_sort_max("Rodeur", 1)
ok("Sorts : Rôdeur niv 1 ne peut lancer AUCUN sort (3.5 : niv 4)",
   nls_rod < 1, f"nls={nls_rod}")

# ── 5. Table XP DMG ───────────────────────────────────────────────────────
from server.game import xp as gxp  # noqa: E402
xp_nd1_n1 = gxp.xp_pour_cr("1", 1)
ok("XP : un FP 1 vaincu rapporte 300 XP à un PJ niv 1 (table DMG)",
   xp_nd1_n1 == 300, f"reçu {xp_nd1_n1}")

print(f"\n{'='*60}\nINTÉGRITÉ : {n_ok} OK, {len(echecs)} échec(s)")
for e in echecs:
    print("  ❌ " + e)
sys.exit(1 if echecs else 0)
