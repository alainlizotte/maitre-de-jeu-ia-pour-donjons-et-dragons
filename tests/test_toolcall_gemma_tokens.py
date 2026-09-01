"""Régression : appels d'outils en format natif Gemma avec jetons canal
`<|"|>` (qui remplacent les guillemets et corrompent le parsing des args).

Sans normalisation, les arguments aboutissaient VIDES ou contaminés par les
jetons (monstres `engager_combat` refusés, attaque sans cible...).
"""

import sys

from server.llm import orchestrator as o
from server.tools.registry import discover_tools

Q = '<|"|>'

TOOLS = None


def _tools():
    global TOOLS
    if TOOLS is None:
        TOOLS = discover_tools()
    return TOOLS


def test_norm_gemma_quote_tokens():
    assert o._norm_gemma_quote_tokens(None) is None
    assert o._norm_gemma_quote_tokens("") == ""
    s = "monstres:" + Q + "Gobelin, Gobelin" + Q
    out = o._norm_gemma_quote_tokens(s)
    assert Q not in out
    assert out == 'monstres:"Gobelin, Gobelin"'


def test_parse_blob_contamine_corrompu_avant_fix():
    # Preuve que la normalisation change le résultat : le blob brut garde
    # les jetons collés à la valeur (→ résolution de monstre échouée).
    raw = "monstres:" + Q + "Gardien de l'Ordre du Phénix, Gardien de l'Ordre du Phénix" + Q
    args_raw = o._parse_args_blob_colon(raw)
    assert args_raw["monstres"].startswith("<|")
    assert args_raw["monstres"].endswith("|>")
    args_norm = o._parse_args_blob_colon(o._norm_gemma_quote_tokens(raw))
    assert args_norm["monstres"] == "Gardien de l'Ordre du Phénix, Gardien de l'Ordre du Phénix"
    assert "<|" not in args_norm["monstres"]


def test_parse_des_quatre_appels_gemma_reels():
    text = (
        "<tool_call>engager_combat{monstres:" + Q + "Gardien de l'Ordre du Phénix, "
        "Gardien de l'Ordre du Phénix" + Q + "}</tool_call>"
        "<tool_call>lancer_attaque{attaquant:" + Q + "Alionor Arcanis" + Q + ","
        "cible:" + Q + "Gardien de l'Ordre du Phénix" + Q + ","
        "raison:" + Q + "Lancer une pierre pour distraire et désarmer le gardien." + Q + "}"
        "</tool_call>"
        "<tool_call>lancer_d20{competence:" + Q + "Perception" + Q + ","
        "difficulte:" + Q + "15" + Q + ","
        "nom_personnage:" + Q + "Alionor Arcanis" + Q + ","
        "raison:" + Q + "Observation d'une faille dans la sécurité de l'Ordre du Phénix au quai." + Q + "}"
        "</tool_call>"
        "<tool_call>inventaire_consulter{nom:" + Q + "Alionor Arcanis" + Q + "}</tool_call>"
    )
    calls, _cleaned = o.parse_prose_brace_calls(text, _tools())
    assert len(calls) == 4

    by_name = {c["name"]: c["arguments"] for c in calls}

    assert by_name["engager_combat"]["monstres"] == (
        "Gardien de l'Ordre du Phénix, Gardien de l'Ordre du Phénix"
    )
    assert "<|" not in by_name["engager_combat"]["monstres"]

    att = by_name["lancer_attaque"]
    assert att["nom_attaquant"] == "Alionor Arcanis"
    assert att["nom_cible"] == "Gardien de l'Ordre du Phénix"

    d20 = by_name["lancer_d20"]
    assert d20["competence"] == "Perception"
    assert d20["difficulte"] == "15"
    assert d20["nom_personnage"] == "Alionor Arcanis"
    assert "Observation d'une faille" in d20["raison"]

    assert by_name["inventaire_consulter"]["nom"] == "Alionor Arcanis"


def test_disambiguation_cible_vers_nom_cible():
    # `cible` est ambigu (ca_cible ET nom_cible) : on préfère le « nom_<x> ».
    spec = _tools()["lancer_attaque"]
    args, _notes = o.sanitize_tool_args(spec, {"cible": "Gobelin", "attaquant": "Borin"})
    assert args.get("nom_cible") == "Gobelin"
    assert args.get("nom_attaquant") == "Borin"
    assert "ca_cible" not in args


def test_strip_gemma_tokens_de_la_narration():
    # Les jetons ne doivent JAMAIS fuir vers le joueur.
    narration = "Le gardien approche " + Q + "d'un air menaçant" + Q + "."
    out = o.strip_narration_artifacts(narration, _tools())
    assert Q not in out
