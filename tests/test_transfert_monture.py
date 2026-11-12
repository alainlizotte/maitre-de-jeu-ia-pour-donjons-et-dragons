# -*- coding: utf-8 -*-
"""Test : inventaire_transferrer — monture / autre PJ / PNJ / sol.

Le scénario b1152b10 : le joueur voulait déposer son sac (et ses 10 000
pc, 90,7 kg !) dans les sacoches du mulet — aucun tool n'existait, le MJ
n'avait pu que narrer. Désormais :
  - destination="monture" : le cargo de selle (capacité PHB 3.5) ;
  - destination="sol" : jeté au sol, ramassable avec inventaire_ramasser ;
  - destination=<autre PJ> : don réel entre fiches ;
  - objet="or" : la monnaie (lourde) voyage aussi.

Usage : py -m pytest tests/test_transfert_monture.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_TOOLS = discover_tools()


async def _creer_pj(d: str, nom: str, joueur: str, or_pc: int = 0) -> None:
    st = PartyState(data_dir=d, partie_id="trsf")
    etat = st.load()
    etat["phase"] = "exploration"
    pjs = etat.setdefault("pj", [])
    pjs.append({"nom": nom, "joueur": joueur, "pv": 14, "pv_max": 14})
    st.save(etat)
    r = await invoke_tool(_TOOLS["fiche_perso_creer_rapide"], _ctx(d), {
        "nom": nom, "race": "Humain", "classe": "Guerrier",
        "niveau": 1, "joueur": joueur,
        "carac_texte": "FOR 16, DEX 12, CON 14, INT 10, SAG 10, CHA 8"})
    assert r.text.startswith(("✅", "ℹ️")), r.text[:150]
    if or_pc:
        fp = os.path.join(d, "fiches", f"fiche_{nom.lower()}.json")
        with open(fp, "r", encoding="utf-8") as f:
            fiche = json.load(f)
        fiche["or"] = or_pc
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)


def _ctx(d: str, joueur: str = "alain") -> ToolContext:
    return ToolContext(partie_id="trsf", joueur=joueur, data_dir=d)


def _fiche(d: str, nom: str) -> dict:
    fp = os.path.join(d, "fiches", f"fiche_{nom.lower()}.json")
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _ecraser_fiche(d: str, nom: str, fiche: dict) -> None:
    fp = os.path.join(d, "fiches", f"fiche_{nom.lower()}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False)


async def _acheter_mulet(d: str, nom: str) -> None:
    """Un mulet possédé (entrée catalogue, poids 0) dans l'inventaire."""
    r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
        "nom": nom, "objet": "Âne ou mulet", "quantite": 1,
        "portee": "permanent"})
    assert not r.text.startswith(("❌", "⚠️")), r.text[:150]


# ------------------------------------------------------------------ #
# 1. Le bât de selle : la charge s'allège RÉELLEMENT
# ------------------------------------------------------------------ #


def test_transfert_vers_monture_allegement_reel():
    """La Tente (9,07 kg) passe sur le bât : hors du dos du PJ."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_a_")
        try:
            await _creer_pj(d, "Throkmar", "alain")
            await _acheter_mulet(d, "Throkmar")
            r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Tente", "quantite": 1,
                "portee": "permanent"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            f1 = _fiche(d, "Throkmar")
            poids_avant = f1.get("poids_transporte")

            r2 = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                   {"nom": "Throkmar", "objet": "Tente",
                                    "quantite": 1, "destination": "monture"})
            assert not r2.text.startswith(("❌", "⚠️")), r2.text[:200]
            assert "bât" in r2.text, r2.text[:200]
            f2 = _fiche(d, "Throkmar")
            noms = [str(i.get("nom")) for i in (f2.get("inventaire") or [])]
            assert "Tente" not in noms, noms
            # la charge a baissé d'autant
            assert (f2.get("poids_transporte") or 0) < poids_avant, (
                poids_avant, f2.get("poids_transporte"))
            # le cargo de la monture le porte
            mont = (f2.get("montures") or [])[0]
            assert "mulet" in str(mont.get("nom", "")).lower()
            assert any("Tente" in str(i.get("nom"))
                       for i in (mont.get("inventaire") or []))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_capacite_monture_depassee_refusee():
    """Un cargo au-delà de la charge lourde du mulet (313 kg) : refusé."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_b_")
        try:
            await _creer_pj(d, "Throkmar", "alain")
            await _acheter_mulet(d, "Throkmar")
            # une pierre de 400 kg dans l'inventaire
            r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Bloc de granit",
                "quantite": 1, "poids": 400.0, "portee": "permanent"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            r2 = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                   {"nom": "Throkmar",
                                    "objet": "Bloc de granit",
                                    "quantite": 1, "destination": "monture"})
            assert "s'effondrerait" in r2.text, r2.text[:200]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 2. La monnaie (le cas b1152b10 : 10 000 pc = 90,7 kg sur le dos)
# ------------------------------------------------------------------ #


def test_monnaie_vers_le_bat():
    """objet="or", quantite=1000 (po) → 10 000 pc quittent le dos."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_c_")
        try:
            await _creer_pj(d, "Throkmar", "alain", or_pc=10000)
            await _acheter_mulet(d, "Throkmar")
            f1 = _fiche(d, "Throkmar")
            poids_avant = f1.get("poids_transporte")
            r = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                  {"nom": "Throkmar", "objet": "or",
                                   "quantite": 1000, "destination": "monture"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:200]
            f2 = _fiche(d, "Throkmar")
            assert int(f2.get("or", 0)) == 0, f2.get("or")
            # ~90,7 kg de pièces quittent le dos
            assert (f2.get("poids_transporte") or 0) <= (
                poids_avant - 90), (poids_avant, f2.get("poids_transporte"))
            mont = (f2.get("montures") or [])[0]
            bourses = [i for i in (mont.get("inventaire") or [])
                       if int(i.get("pc", 0) or 0) == 10000]
            assert bourses, mont
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_monnaie_donnee_a_un_pj_et_a_un_pnj():
    """or → autre PJ (crédité) ; or → PNJ (quitte la table)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_d_")
        try:
            await _creer_pj(d, "Throkmar", "alain", or_pc=5000)
            await _creer_pj(d, "Branda", "pip")
            r = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                  {"nom": "Throkmar", "objet": "or",
                                   "quantite": 200, "destination": "Branda"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:200]
            assert int(_fiche(d, "Throkmar").get("or", 0)) == 3000
            assert int(_fiche(d, "Branda").get("or", 0)) == 2000
            r2 = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                   {"nom": "Throkmar", "objet": "or",
                                    "quantite": 300,
                                    "destination": "Cassyt"})
            assert not r2.text.startswith(("❌", "⚠️")), r2.text[:200]
            assert int(_fiche(d, "Throkmar").get("or", 0)) == 0
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 3. Le don entre PJ et le don à un PNJ (objets)
# ------------------------------------------------------------------ #


def test_don_objet_a_un_autre_pj():
    """La corde passe de Throkmar à Branda (fiches mises à jour)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_e_")
        try:
            await _creer_pj(d, "Throkmar", "alain")
            await _creer_pj(d, "Branda", "pip")
            r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Corde de chanvre (15 m)", "quantite": 1,
                "portee": "permanent"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            r2 = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                   {"nom": "Throkmar", "objet": "corde",
                                    "quantite": 1, "destination": "Branda"})
            assert not r2.text.startswith(("❌", "⚠️")), r2.text[:200]
            noms_t = [str(i.get("nom"))
                      for i in (_fiche(d, "Throkmar").get("inventaire") or [])]
            noms_b = [str(i.get("nom"))
                      for i in (_fiche(d, "Branda").get("inventaire") or [])]
            assert not any("corde" in n.lower() for n in noms_t), noms_t
            assert any("corde" in n.lower() for n in noms_b), noms_b
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_don_objet_a_un_pnj():
    """Destination inconnue de la table = PNJ : l'objet quitte l'inventaire."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_f_")
        try:
            await _creer_pj(d, "Throkmar", "alain")
            r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Fiole sombre", "quantite": 1,
                "portee": "quete"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            r2 = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                   {"nom": "Throkmar", "objet": "fiole",
                                    "quantite": 1, "destination": "Cassyt"})
            assert not r2.text.startswith(("❌", "⚠️")), r2.text[:200]
            noms = [str(i.get("nom"))
                    for i in (_fiche(d, "Throkmar").get("inventaire") or [])]
            assert not any("fiole" in n.lower() for n in noms), noms
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 4. Jeter au sol + ramasser
# ------------------------------------------------------------------ #


def test_jeter_au_sol_puis_ramasser():
    """Tente jetée au sol dans le donjon → ramassable dans CE lieu."""

    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_g_")
        try:
            await _creer_pj(d, "Throkmar", "alain")
            await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Tente", "quantite": 1,
                "portee": "permanent"})
            # un donjon actif (lieu clé du sol)
            st = PartyState(data_dir=d, partie_id="trsf")
            etat = st.load()
            etat["donjon"] = {"id": "Catacombes test", "courant": [1, 0]}
            st.save(etat)
            r = await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d),
                                  {"nom": "Throkmar", "objet": "Tente",
                                   "quantite": 1, "destination": "sol"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:200]
            etat = PartyState(data_dir=d, partie_id="trsf").load()
            sol = (etat.get("objets_au_sol") or {}).get(
                "donjon:Catacombes test:1,0") or []
            assert any("Tente" in str(i.get("nom")) for i in sol), sol
            noms = [str(i.get("nom"))
                    for i in (_fiche(d, "Throkmar").get("inventaire") or [])]
            assert "Tente" not in noms, noms
            # ramassage dans le même lieu
            r2 = await invoke_tool(_TOOLS["inventaire_ramasser"], _ctx(d), {
                "nom": "Throkmar", "objet": "Tente", "quantite": 1,
                "source": "le sol"})
            assert not r2.text.startswith(("❌", "⚠️")), r2.text[:200]
            assert "ramassé du sol" in r2.text, r2.text[:200]
            noms = [str(i.get("nom"))
                    for i in (_fiche(d, "Throkmar").get("inventaire") or [])]
            assert "Tente" in noms, noms
            etat = PartyState(data_dir=d, partie_id="trsf").load()
            sol = (etat.get("objets_au_sol") or {}).get(
                "donjon:Catacombes test:1,0") or []
            assert not sol, sol
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 5. La consultation montre le cargo de la monture
# ------------------------------------------------------------------ #


def test_consulter_affiche_le_cargo():
    async def _run():
        d = tempfile.mkdtemp(prefix="trsf_h_")
        try:
            await _creer_pj(d, "Throkmar", "alain")
            await _acheter_mulet(d, "Throkmar")
            await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Tente", "quantite": 1,
                "portee": "permanent"})
            await invoke_tool(_TOOLS["inventaire_transferrer"], _ctx(d), {
                "nom": "Throkmar", "objet": "Tente", "quantite": 1,
                "destination": "monture"})
            r = await invoke_tool(_TOOLS["inventaire_consulter"], _ctx(d), {
                "nom": "Throkmar"})
            assert "cargo de selle" in r.text, r.text[:300]
            assert "Tente" in r.text, r.text[:300]
            assert "inventaire_transferrer" in r.text, r.text[-400:]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())
