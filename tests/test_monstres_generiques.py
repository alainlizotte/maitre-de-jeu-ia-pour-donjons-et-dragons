"""Refus des monstres PLACEHOLDER (« Mort-vivant de taille M »).

Bug réel (partie 44b02cfc, scénario « Dues For The Dead ») : le MJ a engagé
« Mort-vivant de taille M » — une des 72 fiches placeholder du bestiaire
(nom = type + taille, `type` = « — ») importées d'un corpus DRS. Ces fiches
n'ont ni identité ni illustration correcte : l'image générée était
générique et inadaptée, et le combat tournait autour d'un adversaire jamais
prévu par le module (dont les ennemis officiels sont les morts-vivants
classiques + le Nécromancien rouge).

Le MJ doit se fier au bestiaire (créatures RÉELLES) et aux scénarios
(ennemis officiels) uniquement.

Usage : py -m pytest tests/test_monstres_generiques.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.monstres import (  # noqa: E402
    _est_monstre_generique,
    _suggestions_meme_type,
    monstre_consulter,
)
from server.tools.state import engager_combat  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BESTIAIRE = ROOT / "server" / "data" / "bestiaire.json"


def _ctx(quete_ennemis: list[str] | None = None) -> ToolContext:
    tmp = tempfile.mkdtemp(prefix="dnd35_gen_")
    best_dir = Path(tmp) / "bestiaire.json"
    shutil.copy(BESTIAIRE, best_dir)
    etat = {
        "phase": "exploration",
        "pj": [{"nom": "Barouk", "pv": 12, "pv_max": 18}],
    }
    if quete_ennemis is not None:
        etat["quete"] = {
            "titre": "Dues For The Dead",
            "bible": {"ennemis": quete_ennemis},
        }
    PartyState(data_dir=tmp, partie_id="t_gen").save(etat)
    return ToolContext(partie_id="t_gen", joueur="alain", data_dir=tmp)


# --------------------------------------------------------------------------- #
#  Détection des placeholders
# --------------------------------------------------------------------------- #
def test_detection_placeholder_vs_reels():
    import json as _json

    raw = _json.loads(BESTIAIRE.read_text(encoding="utf-8"))
    assert _est_monstre_generique(raw["mort-vivant_de_taille_m"])
    assert _est_monstre_generique(raw["animal_de_taille_p"])
    assert not _est_monstre_generique(raw["zombie"])
    assert not _est_monstre_generique(raw["squelette"])


def test_suggestions_meme_type_propose_vrais_mortvivants():
    raw = json.loads(BESTIAIRE.read_text(encoding="utf-8"))
    ctx = _ctx()
    sugg = _suggestions_meme_type(ctx, raw["mort-vivant_de_taille_m"])
    assert sugg, "des monstres réels du type Mort-vivant doivent être proposés"
    assert any("Zombie" in s or "Squelette" in s for s in sugg)


# --------------------------------------------------------------------------- #
#  Refus dans les tools de combat / consultation
# --------------------------------------------------------------------------- #
def test_engager_combat_refuse_placeholder_et_cite_le_scenario():
    ctx = _ctx(quete_ennemis=["Nécromancien rouge"])
    r = asyncio.run(engager_combat(ctx, monstres="Mort-vivant de taille M"))
    assert "PLACEHOLDER" in r.text
    assert "Nécromancien rouge" in r.text          # orientation scénario
    # Aucun combat créé.
    etat = PartyState(data_dir=ctx.data_dir, partie_id="t_gen").load()
    assert etat.get("phase") == "exploration"


def test_engager_combat_autorise_gabarit_referencé_par_scénario():
    """Les scénarios utilisent les gabarits comme créatures officielles
    (« Crypts Kelemvor » : flameskull → Mort-vivant de taille M). Quand le
    donjon chargé déclare ce gabarit dans les ennemis d'une salle, il est
    autorisé — sinon la rencontre légitime est cassée."""
    tmp = tempfile.mkdtemp(prefix="dnd35_gen_")
    shutil.copy(BESTIAIRE, Path(tmp) / "bestiaire.json")
    PartyState(data_dir=tmp, partie_id="t_gen").save({
        "phase": "exploration",
        "pj": [{"nom": "Barouk", "pv": 12, "pv_max": 18}],
        "quete": {"titre": "Crypts Kelemvor",
                  "bible": {"ennemis": ["Nécromancien rouge"]}},
        "donjon": {"id": "Crypts Kelemvor", "grille": [{
            "x": 0, "y": 0, "type": "sarcophage",
            "ennemis": ["Mort-vivant_de_taille_M ×1"],
        }]},
    })
    ctx = ToolContext(partie_id="t_gen", joueur="alain", data_dir=tmp)
    r = asyncio.run(engager_combat(ctx, monstres="Mort-vivant de taille M"))
    assert "PLACEHOLDER" not in r.text
    etat = PartyState(data_dir=tmp, partie_id="t_gen").load()
    assert etat.get("phase") == "combat"


def test_engager_combat_accepte_une_creature_reelle():
    ctx = _ctx(quete_ennemis=["Nécromancien rouge"])
    r = asyncio.run(engager_combat(ctx, monstres="Zombie"))
    assert "PLACEHOLDER" not in r.text
    assert not r.text.startswith("⛔")
    etat = PartyState(data_dir=ctx.data_dir, partie_id="t_gen").load()
    assert etat.get("phase") == "combat"


def test_monstre_consulter_refuse_placeholder():
    ctx = _ctx()
    r = asyncio.run(monstre_consulter(ctx, nom="Mort-vivant de taille M"))
    assert "PLACEHOLDER" in r.text


def test_monstre_consulter_refuse_placeholder_meme_engagé():
    """Le refus est INCONDITIONNEL vis-à-vis de `monstres_combat` : avec le
    blocage à l'engagement, plus aucun placeholder ne doit arriver sur le
    plateau — et s'il en reste un dans un vieil état (partie antérieure au
    garde-fou), la consultation reste bloquée au lieu d'être accommodée.
    Seule une RÉFÉRENCE DE SCÉNARIO (bible, donjon) débloque."""
    tmp = tempfile.mkdtemp(prefix="dnd35_gen_")
    shutil.copy(BESTIAIRE, Path(tmp) / "bestiaire.json")
    PartyState(data_dir=tmp, partie_id="t_gen").save({
        "phase": "combat",
        "pj": [{"nom": "BBB", "pv": 15, "pv_max": 15}],
        "monstres_combat": [{
            "nom": "Mort-vivant de taille M", "pv": 20, "pv_max": 26,
            "ca": 15, "fp": "3",
        }],
    })
    ctx = ToolContext(partie_id="t_gen", joueur="alain", data_dir=tmp)
    r = asyncio.run(monstre_consulter(ctx, nom="Mort-vivant de taille M"))
    assert "PLACEHOLDER" in r.text


def test_monstre_consulter_autorise_placeholder_referencé_par_scénario():
    """Même état « placeholder engagé », mais le DONJON chargé référence le
    gabarit (Crypts Kelemvor) → la consultation est légitime."""
    tmp = tempfile.mkdtemp(prefix="dnd35_gen_")
    shutil.copy(BESTIAIRE, Path(tmp) / "bestiaire.json")
    PartyState(data_dir=tmp, partie_id="t_gen").save({
        "phase": "combat",
        "pj": [{"nom": "BBB", "pv": 15, "pv_max": 15}],
        "monstres_combat": [{
            "nom": "Mort-vivant de taille M", "pv": 20, "pv_max": 26,
            "ca": 15, "fp": "3",
        }],
        "donjon": {"id": "Crypts Kelemvor", "grille": [{
            "x": 0, "y": 0, "type": "sarcophage",
            "ennemis": ["Mort-vivant_de_taille_M ×1"],
        }]},
    })
    ctx = ToolContext(partie_id="t_gen", joueur="alain", data_dir=tmp)
    r = asyncio.run(monstre_consulter(ctx, nom="Mort-vivant de taille M"))
    assert "PLACEHOLDER" not in r.text


# --------------------------------------------------------------------------- #
#  Anti-répétition en combat : seuil relâché pour les actions répétées
# --------------------------------------------------------------------------- #
from server.llm.client import Message  # noqa: E402
from server.llm.orchestrator import trouve_repetition  # noqa: E402

ROUND_N = (
    "Vous portez un coup terrible avec votre hache à deux mains. Le métal "
    "heurte la chair corrompue du mort-vivant avec un bruit sec et gluant. "
    "La créature hurle un grognement rauque, ses dents acérées grinçant "
    "contre la pierre. **Que faites-vous ?**"
)
ROUND_N1 = (
    "Vous portez un coup terrible avec votre hache à deux mains. Le métal "
    "heurte la chair corrompue du mort-vivant avec un bruit sec et gluant. "
    "La créature chancelle mais reste debout, ses dents acérées grinceront "
    "encore. **Que faites-vous ?**"
)


def test_combat_rounds_similaires_pas_une_repetition():
    """Deux rounds d'attaque successifs se ressemblent : avec le seuil combat (0.75), la narration du round N+1 n'est plus rejetée comme écho."""
    hist = [Message(role="assistant", content=ROUND_N)]
    # Au seuil par défaut (exploration), l'écho EST détecté…
    assert trouve_repetition(ROUND_N1, hist) is not None
    # …mais PAS au seuil combat.
    assert trouve_repetition(ROUND_N1, hist, seuil=0.75) is None


def test_combat_copie_verbatim_reste_detectee():
    hist = [Message(role="assistant", content=ROUND_N)]
    assert trouve_repetition(ROUND_N, hist, seuil=0.75) is not None
