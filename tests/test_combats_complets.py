"""Scénario complet de combats D&D 3.5 — déterministe, sans LLM.

Déroulé :
  Setup  : création des 4 PJ — Groth (barbare), Mélodie (barde),
           Elara (magicienne), Zarkon (sorcier) — stats et valeurs dérivées
           vérifiées contre les tables officielles 3.5 (PV, CA, BBA, saves).
  Combat 1 : Gobelin + Gobelin (homonymes) — attaque directe du barbare,
           désambiguïsation des cibles, mort des ennemis (Détruit), clôture.
  Combat 2 : Squelette + Zombie — magie (projectile magique, mains brûlantes
           avec sauvegarde Réflexes), contre-attaque du zombie sur un PJ,
           soins du barde (soins légers, plafond pv_max, conditions levées).
  Combat 3 : Ogre — mort d'un PJ par étapes officielles (0 PV = Invalide,
           < 0 = Mourant, -10 = Mort), exclusion du mort à l'initiative
           suivante, réanimation partielle impossible (Mort persiste).
  Rattrapage : _appliquer_degats_oublies applique les dégâts que le LLM a
           jetés mais pas appliqués — jamais deux fois (appariement).

Vérifie en continu : chaque jet affiche TOUJOURS son total (« Dégâts
infligés : N » / « récupère N PV »), chaque application retombe sur les
fiches (PJ) et monstres_combat (ennemis), les state_patches portent les PV
(synchro UI temps réel), les tours d'initiative avancent et bouclent.

Usage : py -m pytest tests/test_combats_complets.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_combats"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RE_DEGATS = re.compile(r"Dégâts infligés\s*:\s*(\d+)")
RE_SOIN = re.compile(r"récupère (\d+) PV → PV (-?\d+)/(\d+)")
RE_PV_MONSTRE = re.compile(r"PV (-?\d+)/(\d+)")

TOUCHE = ("✅ **Touché**", "⭐ **20 naturel**")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_combat_")
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="test", data_dir=d)


async def tool(d: str, nom_outil: str, **args):
    """Invoque un tool par nom et renvoie son ToolResult."""
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


def _etat(d: str) -> dict:
    with open(os.path.join(d, f"partie_{PID}.json"), encoding="utf-8") as f:
        return json.load(f)


def _fiche(d: str, nom: str) -> dict:
    import unicodedata
    nf = unicodedata.normalize("NFKD", nom)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_").lower()
    with open(os.path.join(d, "fiches", f"fiche_{slug}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _seed_for_d20(valeur: int) -> int:
    """Trouve une graine random telle que le prochain randint(1,20) == valeur."""
    for seed in range(10000):
        random.seed(seed)
        if random.randint(1, 20) == valeur:
            return seed
    raise AssertionError(f"aucune graine trouvée pour d20={valeur}")


def _seed_for_dice(nb: int, faces: int, somme: int) -> int:
    """Graine telle que les nb prochains randint(1,faces) totalisent somme."""
    cible = somme - nb  # +1 par dé
    for seed in range(10000):
        random.seed(seed)
        if cible == sum(random.randint(1, faces) - 1 for _ in range(nb)):
            return seed
    raise AssertionError(f"aucune graine trouvée pour {nb}d{faces}={somme}")


# --------------------------------------------------------------------------- #
#  Setup : création des 4 personnages
# --------------------------------------------------------------------------- #
PERSOS = [
    # (nom, race, classe, carac, pv_attendus, ca_attendue, bab, vig, ref, vol)
    ("Groth", "Humain", "Barbare",
     {"FOR": 16, "DEX": 14, "CON": 16, "INT": 10, "SAG": 12, "CHA": 8},
     12 + 3, 12, 1, 5, 2, 1),
    ("Mélodie", "Halfeling", "Barde",
     {"FOR": 8, "DEX": 16, "CON": 12, "INT": 12, "SAG": 10, "CHA": 15},
     6 + 1, 13, 0, 1, 5, 2),
    ("Elara", "Elfe", "Magicienne",
     {"FOR": 8, "DEX": 14, "CON": 12, "INT": 16, "SAG": 12, "CHA": 10},
     4 + 1, 12, 0, 1, 2, 3),
    ("Zarkon", "Humain", "Sorcier",
     {"FOR": 8, "DEX": 14, "CON": 12, "INT": 10, "SAG": 12, "CHA": 16},
     4 + 1, 12, 0, 1, 2, 3),
]


async def _creer_equipe(d: str) -> None:
    for nom, race, classe, caracs, *_ in PERSOS:
        # carac_texte explicites → fiches déterministes (pas de tirage aléatoire)
        carac_texte = ", ".join(
            f"{k.title()} {v}" for k, v in caracs.items())
        r = await tool(d, "fiche_perso_creer_rapide",
                       nom=nom, race=race, classe=classe, niveau=1,
                       joueur=nom.lower(), carac_texte=carac_texte)
        assert r.text.startswith("✅"), f"création {nom}: {r.text}"
    etat = _etat(d)
    assert len(etat["pj"]) == 4, "les 4 PJ doivent être dans l'état de partie"
    assert etat["phase"] == "opening_complete", (
        "la création d'un PJ doit faire passer la phase opening → "
        f"opening_complete (reçu : {etat['phase']})"
    )


async def test_creation_des_4_personnages():
    """Barbare, barde, magicienne, sorcier : fiches conformes aux tables 3.5."""
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        for nom, race, classe, caracs, pv, ca, bab, vig, ref, vol in PERSOS:
            f = _fiche(d, nom)
            assert f["classe"] == classe, f"{nom}: classe {f['classe']}"
            assert f["carac"] == caracs, f"{nom}: carac {f['carac']}"
            assert f["pv"] == pv, f"{nom}: PV {f['pv']} ≠ {pv}"
            assert f["pv_max"] == pv
            assert f["ca"] == ca, f"{nom}: CA {f['ca']} ≠ {ca}"
            assert f["bab"] == bab
            sv = f["sauvegardes"]
            assert sv["Vigueur"] == vig, f"{nom}: vig {sv}"
            assert sv["Reflexes"] == ref, f"{nom}: ref {sv}"
            assert sv["Volonte"] == vol, f"{nom}: vol {sv}"
            assert f["conditions"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Combat 1 — Gobelins homonymes : attaque directe, ciblage, mort, clôture
# --------------------------------------------------------------------------- #
async def test_combat1_gobelins_homonymes():
    d = _fresh_dir()
    try:
        await _creer_equipe(d)

        # ── Engagement : 2 gobelins homonymes ────────────────────────────
        r = await tool(d, "engager_combat", monstres="Gobelin, Gobelin")
        assert "⚔️ **Combat engagé ! Tour 1" in r.text
        etat = _etat(d)
        assert etat["phase"] == "combat" and etat["tour"] == 1
        noms_m = [m["nom"] for m in etat["monstres_combat"]]
        assert noms_m == ["Gobelin", "Gobelin (2)"], (
            f"homonymes non désambiguïsés : {noms_m}"
        )
        for m in etat["monstres_combat"]:
            assert m["pv"] == 5 and m["pv_max"] == 5 and m["ca"] == 15
        # Les 4 PJ + 2 monstres = 6 participants, tri décroissant.
        parts = etat["initiative"]
        assert len(parts) == 6
        inits = [p["init"] for p in parts]
        assert inits == sorted(inits, reverse=True)
        assert {p["nom"] for p in parts} >= {"Groth", "Mélodie", "Elara",
                                             "Zarkon"}
        # ⚔️ Ordre d'initiative respecté DÈS le round 1 : si un monstre
        # gagne l'initiative, le serveur joue son tour AVANT tout — le
        # curseur est donc forcément sur un PJ capable d'agir ici.
        assert etat["courant_tour_pour"] in {p["nom"] for p in etat["pj"]}

        # ── Groth (barbare) frappe « Gobelin (2) » jusqu'à sa mort ───────
        bonus_groth = 1 + 3  # BBA + mod FOR
        tue = False
        dmg_cumule = 0
        for _ in range(10):
            ra = await tool(d, "lancer_attaque",
                            nom_attaquant="Groth", arme="Grande hache",
                            bonus_attaque=bonus_groth, nom_cible="Gobelin (2)",
                            ca_cible=15)
            assert "Total attaque :" in ra.text
            if not any(m in ra.text for m in TOUCHE):
                assert ("❌ **Manqué**" in ra.text
                        or "❌ **1 naturel**" in ra.text)
                continue
            rd = await tool(d, "lancer_degats", nb_des=1, faces=8, bonus=3,
                            arme_ou_sort="Grande hache", cible="Gobelin (2)")
            m = RE_DEGATS.search(rd.text)
            assert m, f"total de dégâts absent : {rd.text}"
            total = int(m.group(1))
            assert total >= 3  # 1d8 min 1 + 3
            ri = await tool(d, "fiche_perso_infliger_degats",
                            nom="Gobelin (2)", degats=total)
            assert f"subit {total} dégâts" in ri.text
            assert "PV" in ri.text
            dmg_cumule += total
            mons = {mm["nom"]: mm for mm in _etat(d)["monstres_combat"]}
            g1, g2 = mons["Gobelin"], mons["Gobelin (2)"]
            # Ciblage homonyme : SEUL « Gobelin (2) » perd des PV.
            assert g1["pv"] == 5, f"le 1er gobelin a été touché à tort : {g1}"
            assert g2["pv"] == 5 - dmg_cumule, (
                f"PV g2 {g2['pv']} ≠ 5 - {dmg_cumule}"
            )
            if g2["pv"] <= 0:
                assert "☠️ **DÉTRUIT**" in ri.text
                assert "Détruit" in g2["conditions"]
                tue = True
                break
        assert tue, "Groth n'est pas parvenu à tuer le gobelin (2)"
        # state_patch → synchro UI des PV monstres
        assert ri.state_patch and "monstres_combat" in ri.state_patch

        # ── Puis le premier Gobelin ──────────────────────────────────────
        for _ in range(10):
            ra = await tool(d, "lancer_attaque",
                            nom_attaquant="Groth", arme="Grande hache",
                            bonus_attaque=bonus_groth, nom_cible="Gobelin",
                            ca_cible=15)
            if not any(m in ra.text for m in TOUCHE):
                continue
            rd = await tool(d, "lancer_degats", nb_des=1, faces=8, bonus=3,
                            arme_ou_sort="Grande hache", cible="Gobelin")
            total = int(RE_DEGATS.search(rd.text).group(1))
            await tool(d, "fiche_perso_infliger_degats",
                       nom="Gobelin", degats=total)
            if _etat(d)["monstres_combat"][0]["pv"] <= 0:
                break
        etat = _etat(d)
        assert all("Détruit" in m["conditions"]
                   for m in etat["monstres_combat"]), (
            "tous les ennemis doivent être Détruits"
        )

        # ── Clôture ──────────────────────────────────────────────────────
        rf = await tool(d, "finir_combat")
        assert "Combat terminé" in rf.text
        etat = _etat(d)
        assert etat["phase"] == "exploration"
        assert etat["monstres_combat"] == [] and etat["initiative"] == []
        assert etat["tour"] == 0 and etat["courant_tour_pour"] is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Combat 2 — Crypte : magie, sauvegarde, contre-attaque, soins
# --------------------------------------------------------------------------- #
async def test_combat2_magie_soins_sauvegarde():
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Squelette, Zombie")
        etat = _etat(d)
        mons = {m["nom"]: m for m in etat["monstres_combat"]}
        assert mons["Squelette"]["pv"] == 3 and mons["Zombie"]["pv"] == 5

        # ── Elara : projectile magique (touche automatiquement) ──────────
        # 2 missiles de 1d4+1 → 2d4+2, minimum 4 → le squelette (3 PV) meurt.
        rd = await tool(d, "lancer_degats", nb_des=2, faces=4, bonus=2,
                        arme_ou_sort="Projectile magique", cible="Squelette")
        total = int(RE_DEGATS.search(rd.text).group(1))
        assert total >= 4
        ri = await tool(d, "fiche_perso_infliger_degats",
                        nom="Squelette", degats=total)
        assert "☠️ **DÉTRUIT**" in ri.text, (
            f"le squelette (3 PV) doit mourir sous {total} dégâts : {ri.text}"
        )
        mons = {m["nom"]: m for m in _etat(d)["monstres_combat"]}
        assert "Détruit" in mons["Squelette"]["conditions"]
        assert mons["Squelette"]["pv"] == 3 - total

        # ── Le zombie contre-attaque Elara (CA 12) ────────────────────────
        pv_avant = _fiche(d, "Elara")["pv"]
        ra = await tool(d, "lancer_attaque", nom_attaquant="Zombie",
                        arme="Coup fistuleux", bonus_attaque=1,
                        nom_cible="Elara", ca_cible=12)
        # La CA officielle (fiche d'Elara, 12) doit primer.
        assert "CA 12" in ra.text
        if any(m in ra.text for m in TOUCHE):
            rd = await tool(d, "lancer_degats", nb_des=1, faces=6, bonus=1,
                            arme_ou_sort="Coup fistuleux", cible="Elara")
            total = int(RE_DEGATS.search(rd.text).group(1))
            ri = await tool(d, "fiche_perso_infliger_degats",
                            nom="Elara", degats=total)
            assert f"subit {total} dégâts" in ri.text
            f = _fiche(d, "Elara")
            assert f["pv"] == pv_avant - total
            # Synchro UI : le patch porte le nouveau PV du PJ.
            assert ri.state_patch and any(
                k.endswith(".pv") or k == "pj_updated"
                for k in ri.state_patch
            )
            # État selon la barre de PV (règles 3.5).
            if f["pv"] == 0:
                assert "Invalide" in f["conditions"]
            elif f["pv"] < 0:
                assert "Mourant" in f["conditions"]
        else:
            assert "❌" in ra.text  # raté proprement signalé

        # ── Mélodie (barde) soigne Elara : soins légers 1d8+1 ────────────
        pv_blessée = _fiche(d, "Elara")["pv"]
        rs = await tool(d, "lancer_des", nb_des=1, faces=8, bonus=1,
                        raison="Soins légers d'Elara")
        m = re.search(r"\*\*Total : (\d+)\*\*", rs.text)
        soin = int(m.group(1))
        rsoin = await tool(d, "fiche_perso_soigner", nom="Elara", soin=soin)
        m = RE_SOIN.search(rsoin.text)
        assert m, f"le soin doit toujours indiquer les PV regagnés : {rsoin}"
        assert int(m.group(1)) == soin
        pv_new = int(m.group(2))
        pv_max = int(m.group(3))
        assert pv_new == min(5, pv_blessée + soin)
        assert pv_max == 5
        f = _fiche(d, "Elara")
        assert f["pv"] == pv_new
        if pv_new > 0:
            assert "Mourant" not in f["conditions"]
            assert "Invalide" not in f["conditions"]
        # Soin plafonné : un gros soin s'arrête à pv_max.
        rmax = await tool(d, "fiche_perso_soigner", nom="Elara", soin=99)
        assert "PV 5/5" in rmax.text and "maximum atteint" in rmax.text
        assert _fiche(d, "Elara")["pv"] == 5

        # ── Zarkon (sorcier) : mains brûlantes, Réflexes DD 14 demi-tour ─
        for attempt in range(14):
            pv_z = next(m["pv"] for m in _etat(d)["monstres_combat"]
                        if m["nom"] == "Zombie")
            if pv_z <= 0:
                break
            rd = await tool(d, "lancer_degats", nb_des=1, faces=4, bonus=0,
                            arme_ou_sort="Mains brûlantes", cible="Zombie")
            total = int(RE_DEGATS.search(rd.text).group(1))
            assert total >= 1
            rsav = await tool(d, "lancer_sauvegarde",
                              type_sauvegarde="Réflexes", modificateur=0,
                              difficulte=14, nom_personnage="Zombie",
                              source="Mains brûlantes de Zarkon")
            assert "**Total :**" in rsav.text or "Total" in rsav.text
            if "✅ **Réussite**" in rsav.text or "⭐" in rsav.text:
                total = total // 2  # demi-tour arrondi en dessous
            ri = await tool(d, "fiche_perso_infliger_degats",
                            nom="Zombie", degats=total)
            assert f"subit {total} dégâts" in ri.text
        mons = {m["nom"]: m for m in _etat(d)["monstres_combat"]}
        assert "Détruit" in mons["Zombie"]["conditions"], (
            "le zombie doit finir détruit par les mains brûlantes"
        )
        await tool(d, "finir_combat")
        assert _etat(d)["phase"] == "exploration"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Combat 3 — Ogre : mort d'un PJ par étapes officielles
# --------------------------------------------------------------------------- #
async def test_combat3_mort_dun_pj():
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Ogre")
        mons = _etat(d)["monstres_combat"]
        assert mons[0]["pv"] == 22 and mons[0]["ca"] == 15

        # ── L'ogre frappe Zarkon (5 PV) : les 3 étapes de la mort 3.5 ────
        # Étape 1 → 0 PV : Invalide.
        r = await tool(d, "fiche_perso_infliger_degats", nom="Zarkon",
                       degats=5)
        assert "subit 5 dégâts" in r.text and "PV 0/5" in r.text
        assert "**Invalide**" in r.text
        f = _fiche(d, "Zarkon")
        assert f["pv"] == 0 and "Invalide" in f["conditions"]

        # Étape 2 → PV négatif : Mourant (+ mention jet de stabilisation).
        r = await tool(d, "fiche_perso_infliger_degats", nom="Zarkon",
                       degats=4)
        assert "PV -4/5" in r.text and "**Mourant**" in r.text
        assert "stabilisation" in r.text
        f = _fiche(d, "Zarkon")
        assert f["pv"] == -4 and "Mourant" in f["conditions"]
        assert "Invalide" not in f["conditions"]

        # Un soin relève un mourant : conditions levées, PV > 0.
        r = await tool(d, "fiche_perso_soigner", nom="Zarkon", soin=6)
        assert "récupère 6 PV → PV 2/5" in r.text
        f = _fiche(d, "Zarkon")
        assert f["pv"] == 2 and f["conditions"] == []

        # Étape 3 → -10 : Mort (PV plancher à -10, jamais au-delà).
        r = await tool(d, "fiche_perso_infliger_degats", nom="Zarkon",
                       degats=50)
        assert "PV -10/5" in r.text and "**MORT**" in r.text
        f = _fiche(d, "Zarkon")
        assert f["pv"] == -10 and "Mort" in f["conditions"]

        # La magie ne ressuscite pas : le soin ne lève pas « Mort ».
        r = await tool(d, "fiche_perso_soigner", nom="Zarkon", soin=8)
        f = _fiche(d, "Zarkon")
        assert "Mort" in f["conditions"], "Mort doit persister après soin"
        assert f["pv"] == min(5, -10 + 8)

        # ── Un PJ mort est exclu de l'initiative du combat suivant ───────
        await tool(d, "finir_combat")
        await tool(d, "engager_combat", monstres="Rat géant")
        noms = [p["nom"] for p in _etat(d)["initiative"]]
        assert "Zarkon" not in noms, "un PJ mort ne rejoint pas l'initiative"
        assert {"Groth", "Mélodie", "Elara"} <= set(noms)
        await tool(d, "finir_combat")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Tours d'initiative : avancement, boucle, incrément de round
# --------------------------------------------------------------------------- #
async def test_avancement_des_tours():
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Kobold")
        etat = _etat(d)
        ordre = [p["nom"] for p in etat["initiative"]]
        assert len(ordre) == 5  # 4 PJ + 1 kobold
        assert etat["tour"] == 1
        # ⚔️ Le serveur a pu jouer le tour du kobold s'il a gagné
        # l'initiative : le curseur démarre donc sur un PJ capable d'agir.
        assert etat["courant_tour_pour"] in {
            p["nom"] for p in etat["pj"]
        }
        # Un tour_suivant par participant → retour au départ, round 2.
        depart = etat["courant_tour_pour"]
        for _ in ordre:
            r = await tool(d, "tour_suivant_combat")
            assert "au tour de **" in r.text
        etat = _etat(d)
        assert etat["tour"] == 2
        assert etat["courant_tour_pour"] == depart
        # tour_suivant hors combat → refus propre.
        await tool(d, "finir_combat")
        r = await tool(d, "tour_suivant_combat")
        assert r.text.startswith("❌")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Jets bornés : 20 naturel, 1 naturel, dégâts jamais négatifs
# --------------------------------------------------------------------------- #
async def test_jets_nat_20_nat_1_et_degats_bornes():
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        # 20 naturel → toucher auto + menace de critique.
        random.seed(_seed_for_d20(20))
        r = await tool(d, "lancer_attaque", nom_attaquant="Groth",
                       arme="Grande hache", bonus_attaque=4,
                       nom_cible="Gobelin", ca_cible=25)
        assert "⭐ **20 naturel**" in r.text
        assert "critique" in r.text
        # 1 naturel → maladresse automatique.
        random.seed(_seed_for_d20(1))
        r = await tool(d, "lancer_attaque", nom_attaquant="Groth",
                       arme="Grande hache", bonus_attaque=30,
                       nom_cible="Gobelin", ca_cible=5)
        assert "❌ **1 naturel**" in r.text
        # Dégâts au total négatif → plafonnés à 0 (jamais de soin involontaire).
        random.seed(_seed_for_dice(1, 4, 1))  # dé = 1
        r = await tool(d, "lancer_degats", nb_des=1, faces=4, bonus=-3,
                       arme_ou_sort="Dague rouillée", cible="Groth")
        assert "Dégâts infligés : 0" in r.text
        m = RE_DEGATS.search(r.text)
        assert m and int(m.group(1)) == 0
        ri = await tool(d, "fiche_perso_infliger_degats", nom="Groth",
                        degats=0)
        assert "subit 0 dégâts" in ri.text
        assert _fiche(d, "Groth")["pv"] == 15
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Rattrapage serveur : les dégâts jetés sont TOUJOURS appliqués (une fois)
# --------------------------------------------------------------------------- #
class _NoopClient:
    async def chat(self, *a, **k):
        raise NotImplementedError

    async def stream_chat(self, *a, **k):
        yield ""

    async def ensure_model_loaded(self) -> bool:
        return True


def _orch(d: str):
    from server.llm.orchestrator import Orchestrator
    return Orchestrator(client=_NoopClient(), tools=TOOLS,
                        tool_mode="auto", detect_simulation=True,
                        max_iterations=4)


def _result_avec_trace(trace: list[dict]):
    from server.llm.orchestrator import OrchestratedResult
    r = OrchestratedResult()
    r.tool_calls_trace = trace
    return r


async def test_rattrapage_applique_les_degats_oublies():
    """Le LLM jette les dégâts mais oublie infliger → le serveur applique."""
    from server.main import _appliquer_degats_oublies

    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Gobelin, Gobelin")
        trace = [{
            "name": "lancer_degats", "ok": True,
            "args": {"cible": "Gobelin (2)", "nb_des": 1, "faces": 8,
                     "bonus": 3, "arme_ou_sort": "Grande hache"},
            "text": "💥 **Dégâts** : Grande hache → Gobelin (2)\n"
                    "- **Dégâts infligés : 7**",
        }]
        result = _result_avec_trace(trace)
        txt = await _appliquer_degats_oublies(_orch(d), result, _ctx(d), None)
        assert "subit 7 dégâts" in txt, f"rattrapage muet : {txt!r}"
        mons = {m["nom"]: m for m in _etat(d)["monstres_combat"]}
        assert mons["Gobelin (2)"]["pv"] == 5 - 7
        assert mons["Gobelin"]["pv"] == 5, "l'homonyme ne doit pas bouger"
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_rattrapage_ne_double_pas_si_deja_applique():
    """Chaîne complète LLM (roll puis infliger) → le rattrapage ne rajoute rien."""
    from server.main import _appliquer_degats_oublies

    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Gobelin")
        orch = _orch(d)
        result = _result_avec_trace([])
        tr_roll = await orch.execute_tool_direct(
            "lancer_degats", {"nb_des": 1, "faces": 8, "bonus": 3,
                              "arme_ou_sort": "Grande hache",
                              "cible": "Gobelin"}, _ctx(d), None, result)
        total = int(RE_DEGATS.search(tr_roll.text).group(1))
        await orch.execute_tool_direct(
            "fiche_perso_infliger_degats", {"nom": "Gobelin",
                                            "degats": total},
            _ctx(d), None, result)
        pv_apres_tour = _etat(d)["monstres_combat"][0]["pv"]

        txt = await _appliquer_degats_oublies(orch, result, _ctx(d), None)
        assert txt == "", f"double application évitée, reçu : {txt!r}"
        assert _etat(d)["monstres_combat"][0]["pv"] == pv_apres_tour
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_rattrapage_somme_detouches_en_un_appel():
    """Le LLM applique t1+t2 en un seul infliger → pas de double application."""
    from server.main import _appliquer_degats_oublies

    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Ogre")
        orch = _orch(d)
        result = _result_avec_trace([])
        totaux = []
        for _ in range(2):
            tr = await orch.execute_tool_direct(
                "lancer_degats", {"nb_des": 1, "faces": 8, "bonus": 3,
                                  "arme_ou_sort": "Grande hache",
                                  "cible": "Ogre"}, _ctx(d), None, result)
            totaux.append(int(RE_DEGATS.search(tr.text).group(1)))
        await orch.execute_tool_direct(
            "fiche_perso_infliger_degats",
            {"nom": "Ogre", "degats": sum(totaux)}, _ctx(d), None, result)
        pv_apres_tour = _etat(d)["monstres_combat"][0]["pv"]

        txt = await _appliquer_degats_oublies(orch, result, _ctx(d), None)
        assert txt == ""
        assert _etat(d)["monstres_combat"][0]["pv"] == pv_apres_tour == (
            22 - sum(totaux))
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_rattrapage_deux_touches_un_seul_infliger():
    """Full attack : 2 touches, un seul infliger → la 2e est rattrapée par le
    serveur (total appliqué = t1+t2, exactement une fois chacun)."""
    from server.main import _appliquer_degats_oublies

    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await tool(d, "engager_combat", monstres="Ogre")
        orch = _orch(d)
        result = _result_avec_trace([])
        totaux = []
        for _ in range(2):
            tr = await orch.execute_tool_direct(
                "lancer_degats", {"nb_des": 1, "faces": 8, "bonus": 3,
                                  "arme_ou_sort": "Grande hache",
                                  "cible": "Ogre"}, _ctx(d), None, result)
            totaux.append(int(RE_DEGATS.search(tr.text).group(1)))
        await orch.execute_tool_direct(
            "fiche_perso_infliger_degats",
            {"nom": "Ogre", "degats": totaux[0]}, _ctx(d), None, result)

        txt = await _appliquer_degats_oublies(orch, result, _ctx(d), None)
        assert f"subit {totaux[1]} dégâts" in txt, (
            f"la 2e touche ({totaux[1]}) doit être rattrapée : {txt!r}"
        )
        assert _etat(d)["monstres_combat"][0]["pv"] == 22 - sum(totaux)
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_rattrapage_cible_inconnue_silencieux():
    """Cible inconnue (ni fiche ni monstre suivi) → aucun crash, rien d'appliqué."""
    from server.main import _appliquer_degats_oublies

    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        trace = [{"name": "lancer_degats", "ok": True,
                  "args": {"cible": "Porte renforcée"},
                  "text": "**Dégâts infligés : 5**"}]
        result = _result_avec_trace(trace)
        txt = await _appliquer_degats_oublies(_orch(d), result, _ctx(d), None)
        assert txt == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Robustesse LLM : placeholders joueur, montants invalides
# --------------------------------------------------------------------------- #
async def test_joueur_placeholder_sanitise():
    """Le LLM peut recopier « <pseudo_joueur> » de la docstring : la fiche
    doit retomber sur l'émetteur réel (sinon verrouillage de tour mort)."""
    d = _fresh_dir()
    try:
        r = await tool(d, "fiche_perso_creer_rapide", nom="Groth",
                       race="Humain", classe="Barbare",
                       joueur="<pseudo_joueur>")
        assert r.text.startswith("✅")
        etat = _etat(d)
        assert etat["pj"][0]["joueur"] == "test", (
            f"joueur placeholder non sanitise : {etat['pj'][0]['joueur']!r}"
        )
        # Variantes courantes et vide → toujours l'émetteur réel.
        for ph in ("{pseudo}", "nom_du_joueur", "", "pseudo joueur"):
            await tool(d, "fiche_perso_creer_rapide", nom="X", race="Humain",
                       classe="Guerrier", joueur=ph)
        etat = _etat(d)
        assert all(p["joueur"] == "test" for p in etat["pj"]), (
            f"joueurs non sanitises : {[p['joueur'] for p in etat['pj']]}"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_montants_invalides_messages_clairs():
    """infliger/soigner sans montant → message explicite (pas de crash),
    pour que le LLM retente avec la bonne valeur."""
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        r = await tool(d, "fiche_perso_infliger_degats", nom="Groth",
                       degats="")
        assert r.text.startswith("❌") and "degats=" in r.text, r.text
        r = await tool(d, "fiche_perso_infliger_degats", nom="Groth",
                       degats=None)
        assert r.text.startswith("❌")
        r = await tool(d, "fiche_perso_soigner", nom="Groth", soin="")
        assert r.text.startswith("❌") and "soin=" in r.text, r.text
        # La fiche n'a pas été corrompue.
        assert _fiche(d, "Groth")["pv"] == 15
        # Et les formats tolérés fonctionnent toujours.
        r = await tool(d, "fiche_perso_infliger_degats", nom="Groth",
                       degats="3")
        assert "subit 3 dégâts" in r.text
        assert _fiche(d, "Groth")["pv"] == 12
        r = await tool(d, "fiche_perso_soigner", nom="Groth", soin="2")
        assert "récupère 2 PV" in r.text
        assert _fiche(d, "Groth")["pv"] == 14
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Exécution standalone (py tests/test_combats_complets.py)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import io

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    echecs = 0
    for nom, fn in fns:
        print(f"\n▶ {nom}")
        try:
            asyncio.run(fn())
            print(f"  ✅ {nom}")
        except AssertionError as e:
            echecs += 1
            print(f"  ❌ {nom}: {e}")
    print(f"\n{'✅ SUITE RÉUSSIE' if not echecs else f'❌ {echecs} ÉCHEC(S)'}")
    sys.exit(1 if echecs else 0)
