"""Ajustement TEMPORAIRE de difficulté des combats (partie 87b8f286).

Politique : le MJ ne remplace JAMAIS la créature du scénario par une autre
espèce et ne modifie JAMAIS le bestiaire. Une rencontre inégale s'équilibre
via le paramètre `ajustement` d'`engager_combat` / `combat_ajouter_combattant`
— adaptation qui ne vit QUE dans les entrées `monstres_combat` du combat en
cours et disparaît à sa clôture.

Usage : py -m pytest tests/test_ajustement_temporaire.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.game.combat import _arme_du_bestiaire  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.dice import _ca_officielle  # noqa: E402
from server.tools.monstres import _find_monstre  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402
from server.tools.state import _parser_ajustement  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_ajust"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_ajust_")
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


async def tool(d: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


async def _creer_pj(d: str, nom: str = "Bargoum", niveau: int = 1) -> None:
    r = await tool(
        d, "fiche_perso_creer_rapide",
        nom=nom, race="Demi-orc", classe="Guerrier", niveau=niveau,
        joueur="alain",
        carac_texte="For 16, Dex 14, Con 11, Int 9, Sag 13, Cha 9",
    )
    assert r.text.startswith("✅"), r.text


def _hash_bestiaire(d: str) -> str:
    with open(os.path.join(d, "bestiaire.json"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --------------------------------------------------------------------------- #
#  Parseur de la consigne d'ajustement
# --------------------------------------------------------------------------- #
def test_parser_ajustement_complet():
    aj = _parser_ajustement("pv 30%, attaque -4, dégâts -4, ca -3, fp 2")
    assert aj["actif"]
    assert aj["facteur_pv"] == 0.3
    assert aj["attaque"] == -4
    assert aj["degats"] == -4
    assert aj["ca"] == -3
    assert aj["fp"] == "2"


def test_parser_ajustement_mult_et_vide():
    aj = _parser_ajustement("pv×0.4")
    assert aj["actif"] and abs(aj["facteur_pv"] - 0.4) < 1e-9
    assert not _parser_ajustement("")["actif"]
    assert not _parser_ajustement("rien à signaler")["actif"]


# --------------------------------------------------------------------------- #
#  engager_combat : refus sans ajustement, acceptation AVEC ajustement
# --------------------------------------------------------------------------- #
async def test_dragon_refuse_sans_ajustement_accepte_avec():
    d = _fresh_dir()
    try:
        h_avant = _hash_bestiaire(d)
        await _creer_pj(d)
        await _creer_pj(d, "Alba")  # 2 PJ niveau 1 → plafond FP 5, PV ~50

        # 1) Sans ajustement : refus (comportement inchangé).
        r0 = await tool(d, "engager_combat", monstres="Dragon rouge (jeune)")
        assert r0.text.startswith("⛔"), r0.text
        assert "trop puissants" in r0.text.lower()
        # Le refus oriente vers l'ajustement temporaire, pas la substitution.
        assert "ajustement" in r0.text.lower()

        # 2) Avec ajustement : le DRAGON du scénario est conservé, adapté.
        r = await tool(
            d, "engager_combat", monstres="Dragon rouge (jeune)",
            ajustement="pv 30%, attaque -4, dégâts -4, ca -3, fp 2",
        )
        assert "Combat engagé" in r.text, r.text
        assert "Ajustement temporaire" in r.text
        assert "bestiaire" in r.text.lower() and "pas modifié" in r.text.lower()

        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") == "combat"
        (mc,) = etat["monstres_combat"]
        ref = _find_monstre(_ctx(d), "Dragon rouge (jeune)")
        pv_ref = int(str(ref["pv"]).split("(")[0])
        assert mc["pv"] == mc["pv_max"] == max(1, int(pv_ref * 0.3))
        assert mc["ca"] == max(1, int(ref["ca"]) - 3)
        assert mc["fp"] == "2"
        assert mc["_ajuste"] is True
        assert mc["_aj_attaque"] == -4
        assert mc["_aj_degats"] == -4

        # 3) Le bestiaire est INTACT (aucune écriture).
        assert _hash_bestiaire(d) == h_avant
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_gouverneur_pv_passe_avec_ajustement():
    """Le trio écrasant (182 PV cumulés) est refusé par le gouverneur de PV
    (4 PJ : plafond FP 7 franchi), mais la MÊME rencontre passe avec des PV
    adaptés (temporaires)."""
    d = _fresh_dir()
    try:
        for nom in ("Groth", "Mélodie", "Elara", "Zarkon"):
            await _creer_pj(d, nom)  # 4 PJ niveau 1 → plafond FP 7, PV ~100
        r0 = await tool(
            d, "engager_combat", monstres="Lamie, Oxydeur, Plasme")
        assert "Rencontre écrasante" in r0.text, r0.text

        r = await tool(
            d, "engager_combat", monstres="Lamie, Oxydeur, Plasme",
            ajustement="pv 20%, fp 3",
        )
        assert "Combat engagé" in r.text, r.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        pvs = sorted(m["pv_max"] for m in etat["monstres_combat"])
        assert pvs == [5, 11, 19]  # 27, 58, 97 × 20 % (min 1)
        assert all(m["fp"] == "3" for m in etat["monstres_combat"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Mécanique : CA imposée et attaques auto suivent l'ajustement
# --------------------------------------------------------------------------- #
async def test_ca_officielle_prefere_entree_combat_ajustee():
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        await tool(d, "engager_combat", monstres="Gobelin")
        ref = _find_monstre(_ctx(d), "Gobelin")
        ca_bestiaire = int(ref["ca"])

        # Combat en cours SANS ajustement : CA suivie (= bestiaire),
        # source « combat en cours » (priorité sur le bestiaire).
        ca, src = _ca_officielle(_ctx(d), "Gobelin")
        assert ca == ca_bestiaire
        assert "combat en cours" in src

        # Avec ajustement temporaire : la CA adaptée PRIME sur le bestiaire.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        etat["monstres_combat"][0]["ca"] = 8
        etat["monstres_combat"][0]["_ajuste"] = True
        PartyState(data_dir=d, partie_id=PID).save(etat)
        ca, src = _ca_officielle(_ctx(d), "Gobelin")
        assert ca == 8, (ca, src)
        assert "ajusté" in src
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_attaque_auto_applique_les_deltas():
    import random as _random
    from server.game.combat import _attaque_auto
    from server.game.state import PartyState as PS

    d = _fresh_dir()
    try:
        await _creer_pj(d)
        await _creer_pj(d, "Alba")
        await tool(
            d, "engager_combat", monstres="Dragon rouge (jeune)",
            ajustement="pv 30%, attaque -4, dégâts -4, fp 2",
        )
        etat = PS(data_dir=d, partie_id=PID).load()
        assert etat["monstres_combat"][0]["_ajuste"] is True
        etat["courant_tour_pour"] = "Dragon rouge (jeune)"
        PS(data_dir=d, partie_id=PID).save(etat)

        ref = _find_monstre(_ctx(d), "Dragon rouge (jeune)")
        arme, bonus_atk, nb_des, faces, bonus_dmg = _arme_du_bestiaire(ref)

        # Jets déterministes : attaque 10, dégâts max.
        real = _random.randint

        def _randint(a: int, b: int) -> int:
            return 10 if (a, b) == (1, 20) else b

        _random.randint = _randint
        try:
            from server.game.combat import ResultatBoucle
            res = ResultatBoucle()
            await _attaque_auto(_ctx(d), res, "Dragon rouge (jeune)", ennemi=True)
        finally:
            _random.randint = real

        texte = "\n".join(res.events)
        assert f"Bonus total : {bonus_atk - 4:+d}" in texte, texte
        assert f"Bonus dégâts : {bonus_dmg - 4:+d}" in texte, texte
        # La cible (le PJ) a bien subi les dégâts ajustés.
        etat2 = PS(data_dir=d, partie_id=PID).load()
        assert etat2["pj"][0]["pv"] < etat2["pj"][0]["pv_max"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_renfort_avec_ajustement():
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        await tool(d, "engager_combat", monstres="Gobelin")
        r = await tool(
            d, "combat_ajouter_combattant", nom="Ogre",
            ajustement="pv 25%, attaque -3, dégâts -3, fp 1",
        )
        assert "rejoint le combat" in r.text, r.text
        assert "ajustement temporaire" in r.text.lower()
        etat = PartyState(data_dir=d, partie_id=PID).load()
        ogre = next(m for m in etat["monstres_combat"] if m["nom"].startswith("Ogre"))
        ref = _find_monstre(_ctx(d), "Ogre")
        pv_ref = int(str(ref["pv"]).split("(")[0])
        assert ogre["pv"] == ogre["pv_max"] == max(1, int(pv_ref * 0.25))
        assert ogre["fp"] == "1"
        assert ogre["_aj_attaque"] == -3
    finally:
        shutil.rmtree(d, ignore_errors=True)
