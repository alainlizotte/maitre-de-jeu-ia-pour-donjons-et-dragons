"""Tests — familiers (Magicien/Sorcier) et compagnons animaux (Druide/Rodeur).

Couvre :
- tables de progression PHB 3.5 (familiers p.60/64, compagnons p.48) ;
- profils ajustés au maître : PV moitié, CA + armure naturelle, BBA,
  sauvegardes, compagnon (DV sup., For/Dex, niveau effectif rôdeur −3) ;
- validation des choix du formulaire (`valider_choix`) ;
- couverture bestiaire : chaque espèce référencée doit exister ;
- tools `appeler_familier` (rituel 100 po, refus sans champ, insertion
  combat en allié) et `renvoyer_familier` (Vigueur DD 15, perte XP).

USAGE
-----
    py -m pytest tests/test_familiers.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import familiers as F                                          # noqa: E402
from server.game.state import PartyState                                   # noqa: E402
from server.tools.base import ToolContext, invoke_tool                     # noqa: E402
from server.tools.fiches import _slug                                      # noqa: E402
from server.tools.registry import discover_tools                           # noqa: E402

TOOLS = discover_tools("server.tools")
_PID = "t_fam"

MAGE = {
    "nom": "Zarkon", "race": "Humain", "classe": "Magicien", "niveau": 3,
    "carac": {"FOR": 8, "DEX": 14, "CON": 12, "INT": 17, "SAG": 10, "CHA": 10},
    "pv": 12, "pv_max": 14, "ca": 12, "bab": 1, "or": 1500,
    "sauvegardes": {"Vigueur": 1, "Reflexes": 2, "Volonte": 4},
    "familier": {"type": "familier", "espece": "Chat", "invoque": False},
}
DRUIDE = {
    "nom": "Sylva", "race": "Humaine", "classe": "Druide", "niveau": 5,
    "carac": {"FOR": 12, "DEX": 12, "CON": 14, "INT": 10, "SAG": 16, "CHA": 12},
    "pv": 30, "pv_max": 30, "ca": 14, "bab": 3, "or": 200,
    "sauvegardes": {"Vigueur": 4, "Reflexes": 1, "Volonte": 4},
    "familier": {"type": "compagnon", "espece": "Loup", "invoque": False},
}


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=_PID, joueur="Test", data_dir=d)


def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_fam_")
    shutil.copy2(
        os.path.join(ROOT, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ecrire_fiche(d: str, fiche: dict) -> None:
    fd = Path(d) / "fiches"
    fd.mkdir(parents=True, exist_ok=True)
    (fd / f"fiche_{_slug(fiche['nom'])}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8"
    )


def _lire_fiche(d: str, nom: str) -> dict:
    p = Path(d) / "fiches" / f"fiche_{_slug(nom)}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _etat_combat(pj: dict) -> dict:
    return {
        "phase": "combat",
        "tour": 1,
        "courant_tour_pour": pj["nom"],
        "initiative": [{"nom": pj["nom"], "init": 8}],
        "pj": [{
            "nom": pj["nom"], "pv": pj["pv"], "pv_max": pj["pv_max"],
        }],
        "monstres_combat": [
            {"nom": "Gobelin", "pv": 5, "pv_max": 5, "ca": 15,
             "conditions": []},
        ],
    }


# --------------------------------------------------------------------------- #
#  Tables de progression (PHB 3.5)
# --------------------------------------------------------------------------- #
def test_progression_familier():
    p1 = F.progression_familier(1)
    assert p1["aj_naturelle"] == 1 and p1["int"] == 6
    assert "Lien télépathique" in p1["pouvoirs"]
    p3 = F.progression_familier(3)
    assert p3["aj_naturelle"] == 2 and p3["int"] == 7
    assert "Conduit (porte les sorts de contact)" in p3["pouvoirs"]
    # Cumul : niv.11 → tous les pouvoirs jusqu'à la résistance à la magie.
    p11 = F.progression_familier(11)
    assert p11["aj_naturelle"] == 6 and p11["int"] == 11
    assert "Résistance à la magie (niveau du maître + 5)" in p11["pouvoirs"]
    assert "Vigilance" in p11["pouvoirs"]
    # Plafonds de table (niv.20).
    p20 = F.progression_familier(20)
    assert p20["aj_naturelle"] == 10 and p20["int"] == 15


def test_progression_compagnon():
    c1 = F.progression_compagnon(1)
    assert (c1["dv_sup"], c1["aj_naturelle"], c1["aj_for_dex"], c1["tours"]) == (0, 0, 0, 1)
    assert "Lien" in c1["pouvoirs"]
    c5 = F.progression_compagnon(5)
    assert (c5["dv_sup"], c5["aj_naturelle"], c5["aj_for_dex"], c5["tours"]) == (2, 2, 1, 2)
    assert "Esquive totale" in c5["pouvoirs"]
    c9 = F.progression_compagnon(9)
    assert c9["dv_sup"] == 6 and "Attaques multiples" in c9["pouvoirs"]
    c18 = F.progression_compagnon(18)
    assert c18["dv_sup"] == 12 and c18["tours"] == 7
    assert "Esquive extraordinaire" in c18["pouvoirs"]


def test_niveau_effectif_compagnon():
    assert F.niveau_effectif_compagnon("Druide", 5) == 5
    assert F.niveau_effectif_compagnon("Rodeur", 4) == 1
    assert F.niveau_effectif_compagnon("Rodeur", 10) == 7


# --------------------------------------------------------------------------- #
#  Profils ajustés
# --------------------------------------------------------------------------- #
def test_profil_familier_chat_magicien_3():
    animal = F.charger_animal(str(ROOT / "server" / "data"), "chat")
    p = F.profil_familier(MAGE, animal)
    # PV = moitié des PV du maître (arrondi inférieur).
    assert p["pv"] == MAGE["pv_max"] // 2 == 7
    # CA = espèce (14) + armure naturelle du niveau de classe (niv.3 → +2).
    assert p["ca"] == 16
    # BBA d'attaque = BBA maître (1) + meilleur mod For/Dex (Dex 15 → +2).
    assert p["bba_attaque"] == 3
    assert p["dv"] == 3  # DV remplacés par le niveau du maître
    assert p["int"] == 7
    # Sauvegardes au meilleur des deux (maître vs animal).
    assert p["sauvegardes"]["Reflexes"] == max(2, 4) == 4
    assert p["sauvegardes"]["Volonte"] == max(4, 1) == 4
    assert "Déplacement silencieux" in p["faculte_maitre"]


def test_profil_familier_pv_ne_descendent_pas_sous_1():
    animal = F.charger_animal(str(ROOT / "server" / "data"), "corbeau")
    fiche = {**MAGE, "pv_max": 1}
    assert F.profil_familier(fiche, animal)["pv"] == 1


def test_profil_compagnon_loup_druide_5():
    animal = F.charger_animal(str(ROOT / "server" / "data"), "loup")
    p = F.profil_compagnon(DRUIDE, animal)
    # Loup : 2d8+2 (11 pv), CA 14, For 13, Dex 15. Niv.eff 5 → +2 DV, CA +2,
    # For/Dex +1.
    assert p["pv"] == 11 + 9 + 4  # +4,5×2 arrondi bas + mod Con (+2)×2
    assert p["pv"] == 24
    assert p["ca"] == 16
    assert p["for"] == 14 and p["dex"] == 16
    # BBA moyen sur 4 DV → +3.
    assert p["bba_attaque"] == 3
    assert p["tours"] == 2
    assert p["niveau_effectif"] == 5
    assert "Esquive totale" in p["pouvoirs"]


def test_profil_compagnon_rodeur_4_niveau_effectif_1():
    animal = F.charger_animal(str(ROOT / "server" / "data"), "loup")
    fiche = {
        "nom": "Rik", "classe": "Rodeur", "niveau": 4, "bab": 3,
        "sauvegardes": {"Vigueur": 4, "Reflexes": 4, "Volonte": 1},
    }
    p = F.profil_compagnon(fiche, animal)
    assert p["niveau_effectif"] == 1
    # Aucune progression : profil de l'animal de base.
    assert p["ca"] == animal["ca"]
    assert p["for"] == 13 and p["dex"] == 15


def test_profil_dispatch_selon_fiche():
    best = str(ROOT / "server" / "data")
    chat = F.charger_animal(best, "chat")
    loup = F.charger_animal(best, "loup")
    assert F.profil(MAGE, chat)["type"] == "familier"
    assert F.profil(DRUIDE, loup)["type"] == "compagnon"


# --------------------------------------------------------------------------- #
#  Validation des choix du formulaire
# --------------------------------------------------------------------------- #
def test_valider_choix_familiers_compagnons():
    assert F.valider_choix({"type": "familier", "espece": "Chat"}, "Sorcier", 1) == {
        "type": "familier", "espece": "Chat", "invoque": False,
    }
    assert F.valider_choix(
        {"type": "compagnon", "espece": "Serpent venimeux (moyen)"}, "Druide", 1
    )["espece"] == "Serpent venimeux (moyen)"


def test_valider_choix_refus():
    import pytest

    # Classe sans droit.
    with pytest.raises(ValueError):
        F.valider_choix({"type": "familier", "espece": "Chat"}, "Guerrier", 1)
    # Espèce hors liste familier.
    with pytest.raises(ValueError):
        F.valider_choix({"type": "familier", "espece": "Loup"}, "Magicien", 1)
    # Type incohérent.
    with pytest.raises(ValueError):
        F.valider_choix({"type": "compagnon", "espece": "Chat"}, "Druide", 1)
    # Rodeur trop bas niveau.
    with pytest.raises(ValueError):
        F.valider_choix({"type": "compagnon", "espece": "Loup"}, "Rodeur", 3)
    # Hors-norme trop tôt (tigre : niv.7).
    with pytest.raises(ValueError):
        F.valider_choix({"type": "compagnon", "espece": "Tigre"}, "Druide", 5)
    # Espèce inconnue.
    with pytest.raises(ValueError):
        F.valider_choix({"type": "familier", "espece": "Dragon"}, "Magicien", 1)


def test_valider_choix_hors_norme_ok():
    choix = F.valider_choix({"type": "compagnon", "espece": "Ours noir"}, "Druide", 4)
    assert choix["espece"] == "Ours noir"


def test_especes_disponibles():
    assert len(F.especes_disponibles("Magicien", 1)) == len(F.FAMILIERS)
    assert len(F.especes_disponibles("Druide", 1)) == len(F.COMPAGNONS)
    # Hors-normes débloqués progressivement.
    n4 = len(F.especes_disponibles("Druide", 4))
    n7 = len(F.especes_disponibles("Druide", 7))
    assert n4 > len(F.COMPAGNONS) and n7 > n4
    assert F.especes_disponibles("Guerrier", 10) == []
    # Rodeur : rien avant le niv.4.
    assert F.especes_disponibles("Rodeur", 3) == []
    assert len(F.especes_disponibles("Rodeur", 4)) == len(F.COMPAGNONS)


def test_toutes_les_especes_sont_dans_le_bestiaire():
    best = F.charger_bestiaire(str(ROOT / "server" / "data"))
    for grp in (F.FAMILIERS, F.COMPAGNONS, F.COMPAGNONS_HORS_NORME):
        for e in grp:
            assert e["cle"] in best, f"espèce absente du bestiaire : {e['cle']}"


# --------------------------------------------------------------------------- #
#  Tool : appeler_familier
# --------------------------------------------------------------------------- #
async def test_appeler_refus_sans_champ_familier():
    d = _fresh_dir()
    try:
        fiche = {k: v for k, v in MAGE.items() if k != "familier"}
        _ecrire_fiche(d, fiche)
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert r.text.startswith("⛔"), r.text
        assert "espèce" in r.text.lower()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_refus_mauvaise_classe():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, {**MAGE, "classe": "Guerrier"})
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert r.text.startswith("⛔"), r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_refus_or_insuffisant():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, {**MAGE, "or": 500})  # < 1000 pc (100 po)
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert r.text.startswith("⛔"), r.text
        assert "100 po" in r.text
        # L'état de la fiche est inchangé (pas de lien scellé).
        assert _lire_fiche(d, "Zarkon")["familier"]["invoque"] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_familier_rituel_100_po_hors_combat():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, MAGE)
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert "Chat de Zarkon" in r.text, r.text
        assert "PV **7**" in r.text
        assert "CA **16**" in r.text
        f = _lire_fiche(d, "Zarkon")
        # 100 po déduits : 1500 pc → 500 pc.
        assert f["or"] == 500
        assert f["familier"]["invoque"] is True
        # Second appel : le lien existe déjà, pas de nouveau coût.
        r2 = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert "rituel" not in "\n".join(
            l for l in r2.text.splitlines() if "Composantes" in l
        ).lower() or "Composantes" not in r2.text
        assert _lire_fiche(d, "Zarkon")["or"] == 500
        # Hors combat : pas d'insertion initiative.
        etat = PartyState(data_dir=d, partie_id=_PID).load()
        assert etat.get("phase") != "combat"
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_familier_ignorer_cout():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, {**MAGE, "or": 100})
        r = await invoke_tool(
            TOOLS["appeler_familier"], _ctx(d),
            {"nom_personnage": "Zarkon", "ignorer_cout": True},
        )
        assert "Chat de Zarkon" in r.text, r.text
        assert _lire_fiche(d, "Zarkon")["or"] == 100  # or intact
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_compagnon_druide_sans_cout():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, DRUIDE)
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Sylva"})
        assert "Loup de Sylva" in r.text, r.text
        assert "aucun rituel" in r.text.lower()
        # Or intact, PV du loup ajustés (24 = 11 + 2 DV sup. + mod Con).
        f = _lire_fiche(d, "Sylva")
        assert f["or"] == 200
        assert "PV **24**" in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_familier_rejoint_le_combat():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, MAGE)
        PartyState(data_dir=d, partie_id=_PID).save(_etat_combat(MAGE))
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert "rejoint le combat" in r.text, r.text
        etat = PartyState(data_dir=d, partie_id=_PID).load()
        assert etat["phase"] == "combat"
        chat = next(
            (m for m in etat["monstres_combat"]
             if m["nom"].startswith("Chat de Zarkon")),
            None,
        )
        assert chat is not None
        assert chat["allie"] is True
        assert chat["pv"] == chat["pv_max"] == 7
        assert chat["ca"] == 16
        assert any(e["nom"] == chat["nom"] for e in etat["initiative"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_appeler_rodeur_trop_bas_niveau_refuse():
    d = _fresh_dir()
    try:
        # Le compagnon animal du Rodeur n'arrive qu'au niveau 4 (PHB p.48).
        _ecrire_fiche(d, {
            **DRUIDE, "classe": "Rodeur", "niveau": 3, "bab": 1,
            "familier": {"type": "compagnon", "espece": "Loup", "invoque": False},
        })
        r = await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Sylva"})
        assert r.text.startswith("⛔"), r.text
        assert "niveau 4" in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Tool : renvoyer_familier
# --------------------------------------------------------------------------- #
async def test_renvoyer_sans_familier():
    d = _fresh_dir()
    try:
        _ecrire_fiche(d, {k: v for k, v in MAGE.items() if k != "familier"})
        r = await invoke_tool(TOOLS["renvoyer_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert r.text.startswith("⛔"), r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_renvoyer_familier_perte_xp_plafonnee():
    d = _fresh_dir()
    try:
        fiche = {
            **MAGE,
            "xp": 500,
            "familier": {"type": "familier", "espece": "Chat", "invoque": True},
        }
        _ecrire_fiche(d, fiche)
        r = await invoke_tool(TOOLS["renvoyer_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert "renvoyé" in r.text.lower(), r.text
        assert "Vigueur DD 15" in r.text
        f = _lire_fiche(d, "Zarkon")
        # Jet aléatoire : réussite → −300 XP (200 restants), échec → plafonné
        # aux 500 XP disponibles (0 restant) — jamais négatif.
        if "RÉUSSITE" in r.text:
            assert f["xp"] == 200
        else:
            assert f["xp"] == 0
        assert f["familier"]["invoque"] is False
        assert "an et un jour" in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_renvoyer_compagnon_sans_penalite_xp():
    d = _fresh_dir()
    try:
        fiche = {
            **DRUIDE,
            "xp": 9000,
            "familier": {"type": "compagnon", "espece": "Loup", "invoque": True},
        }
        _ecrire_fiche(d, fiche)
        r = await invoke_tool(TOOLS["renvoyer_familier"], _ctx(d), {"nom_personnage": "Sylva"})
        assert "Loup de Sylva" in r.text, r.text
        assert "Vigueur" not in r.text  # la pénalité XP ne vaut que le familier
        assert _lire_fiche(d, "Sylva")["xp"] == 9000
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_renvoyer_familier_sort_du_combat():
    d = _fresh_dir()
    try:
        fiche = {
            **MAGE,
            "xp": 600,
            "familier": {"type": "familier", "espece": "Chat", "invoque": True},
        }
        _ecrire_fiche(d, fiche)
        PartyState(data_dir=d, partie_id=_PID).save(_etat_combat(MAGE))
        await invoke_tool(TOOLS["appeler_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        r = await invoke_tool(TOOLS["renvoyer_familier"], _ctx(d), {"nom_personnage": "Zarkon"})
        assert "initiative" in r.text.lower() or "Détruit" in r.text, r.text
        etat = PartyState(data_dir=d, partie_id=_PID).load()
        chat = next(
            (m for m in etat["monstres_combat"]
             if m["nom"].startswith("Chat de Zarkon")),
            None,
        )
        assert chat is not None and "Détruit" in chat["conditions"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Le module de modèle sert bien les listes au frontend
# --------------------------------------------------------------------------- #
def test_modele_pour_client():
    m = F.modele_pour_client(str(ROOT / "server" / "data"))
    assert set(m["classes_familier"]) == {"Magicien", "Sorcier"}
    assert m["niveau_min_compagnon"]["Rodeur"] == 4
    assert len(m["familiers"]) == 10
    assert len(m["compagnons_animaux"]) == 14
    chat = next(e for e in m["familiers"] if e["nom"] == "Chat")
    assert chat["pv"] == 2 and chat["ca"] == 14
    assert any(p["niveaux"] == "1-2" for p in m["progression_familier"])
    assert any(p["niveaux"] == "18-20" for p in m["progression_compagnon"])


# Exécution directe (sans pytest) : py tests/test_familiers.py
if __name__ == "__main__":
    for nom, fn in sorted(globals().items()):
        if nom.startswith("test_") and callable(fn):
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
            print(f"OK  {nom}")
