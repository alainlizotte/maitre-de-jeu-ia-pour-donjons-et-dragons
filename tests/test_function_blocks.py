"""Blocs `<function=..><parameter=..>` — rattrapage des appels ChatML/Qwen.

Régression observée en partie 54de40ed : le modèle a émis
`<tool_call><function=inventaire_ajouter><parameter=nom>BBB</parameter>
<item>…</item>…` — avec, après le premier paramètre, des balises d'arguments
NUES (`<item>`, `<quantity>`, `<description>`) au lieu de
`<parameter=…>`. Aucun parseur ne couvrait ce format : l'appel fuyait tel
quel dans la narration montrée au joueur et l'objet n'était JAMAIS ajouté à
l'inventaire. `extract_function_blocks` normalise désormais ce format vers
les tool_calls natifs ; `strip_narration_artifacts` retire tout résidu.

Usage : py -m pytest tests/test_function_blocks.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (  # noqa: E402
    extract_function_blocks,
    extract_toolcall_blocks,
    resolve_tool_name,
    strip_narration_artifacts,
)

# Format EXACT observé en partie (balises d'args mixtes : parameter= + nues).
BLOC_REEL = """📜 Objet trouvé : Livre ancien avec des symboles nécromantiques (à ajouter à l'inventaire de BBB)
<tool_call>
<function=inventaire_ajouter>
<parameter=nom>
BBB
</parameter>
<item>Livre ancien avec des symboles nécromantiques</item>
<quantity>1</quantity>
<description>Livre ancien contenant des informations sur les runes célestes de l'obélisque et les rituels sombres.</description>
</function>"""


def test_bloc_reel_54de40ed():
    calls, cleaned = extract_function_blocks(BLOC_REEL)
    assert len(calls) == 1, calls
    assert calls[0]["name"] == "inventaire_ajouter", calls[0]
    args = calls[0]["arguments"]
    assert args["nom"] == "BBB", args
    assert args["item"] == "Livre ancien avec des symboles nécromantiques", args
    assert args["quantity"] == "1", args
    assert "runes célestes" in args["description"], args
    # Le texte nettoyé ne contient PLUS la moindre syntaxe d'appel.
    for motif in ("<tool_call", "<function=", "<parameter", "</function>",
                  "</parameter>", "<item>", "</item>", "<quantity>"):
        assert motif not in cleaned, (motif, cleaned)
    assert "📜 Objet trouvé" in cleaned, cleaned   # la prose reste


def test_sans_wrapper_et_parametres_standards():
    texte = (
        "Vous fouillez la pièce.\n"
        "<function=lancer_d20>\n"
        "<parameter=modificateur>3</parameter>\n"
        "<parameter=raison>Perception</parameter>\n"
        "</function>\n"
        "Que faites-vous ensuite ?"
    )
    calls, cleaned = extract_function_blocks(texte)
    assert len(calls) == 1 and calls[0]["name"] == "lancer_d20", calls
    assert calls[0]["arguments"] == {
        "modificateur": "3", "raison": "Perception",
    }, calls[0]
    assert "<function=" not in cleaned and "lancer_d20" not in cleaned
    assert "Vous fouillez la pièce." in cleaned
    assert "Que faites-vous ensuite ?" in cleaned


def test_nom_entre_guillemets_et_wrapper_ferme():
    texte = ('<tool_call><function="inventaire_ajouter">'
             '<parameter=nom>BBB</parameter>'
             '</function></tool_call>')
    calls, cleaned = extract_function_blocks(texte)
    assert len(calls) == 1 and calls[0]["name"] == "inventaire_ajouter", calls
    assert "tool_call" not in cleaned and "function" not in cleaned


def test_plusieurs_blocs():
    texte = (
        "<function=lancer_d20><parameter=modificateur>1</parameter></function>"
        " milieu "
        "<function=lancer_des><parameter=nb_des>2</parameter>"
        "<parameter=faces>6</parameter></function>"
    )
    calls, cleaned = extract_function_blocks(texte)
    assert [c["name"] for c in calls] == ["lancer_d20", "lancer_des"], calls
    assert calls[1]["arguments"]["faces"] == "6"
    assert "milieu" in cleaned and "function" not in cleaned


def test_aucun_bloc_texte_intact():
    texte = "Narration parfaitement normale, sans aucun appel."
    calls, cleaned = extract_function_blocks(texte)
    assert calls == [] and cleaned == texte


def test_non_regression_blocs_json():
    """`<tool_call>{json}</tool_call>` reste géré par extract_toolcall_blocks,
    non capté par le nouveau parseur (le corps n'est PAS du `<function=..>`)."""
    texte = '<tool_call>{"name": "lancer_d20", "arguments": {"modificateur": 2}}</tool_call>'
    calls, _ = extract_toolcall_blocks(texte)
    assert len(calls) == 1 and calls[0]["name"] == "lancer_d20", calls
    f_calls, _ = extract_function_blocks(texte)
    assert f_calls == [], f_calls


def test_strip_narration_artifacts_retire_le_bloc():
    """Dernière ligne de défense : même streamée en narration FINALE, la
    syntaxe ne doit jamais atteindre le joueur."""
    out = strip_narration_artifacts(BLOC_REEL)
    for motif in ("<tool_call", "<function=", "<parameter", "</function>",
                  "<item>", "</item>", "<quantity>"):
        assert motif not in out, (motif, out)
    assert "📜 Objet trouvé" in out, out


def test_nom_outil_resolu():
    """Le nom extrait se résout vers le tool réel du registre."""
    outils = {"inventaire_ajouter": object()}
    assert resolve_tool_name("inventaire_ajouter", outils) == "inventaire_ajouter"


# --------------------------------------------------------------------------- #
#  Aliases d'arguments : item→objet, quantity→quantite (54de40ed)
# --------------------------------------------------------------------------- #

import inspect  # noqa: E402

from server.llm.orchestrator import sanitize_tool_args  # noqa: E402


class _SpecInv:
    """Mini-spec mimant inventaire_ajouter(nom, objet, quantite, poids)."""
    expected_args = {
        "nom": inspect.Parameter("nom", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                 annotation=str),
        "objet": inspect.Parameter("objet", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                   annotation=str),
        "quantite": inspect.Parameter("quantite", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                      annotation=int, default=1),
        "poids": inspect.Parameter("poids", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                   annotation=float, default=None),
    }
    resolved_hints = {"nom": str, "objet": str, "quantite": int, "poids": float}


def test_aliases_args_item_quantity():
    args, notes = sanitize_tool_args(_SpecInv, {
        "nom": "BBB",
        "item": "Livre ancien avec des symboles nécromantiques",
        "quantity": "1",
        "description": "runes célestes…",   # param inconnu → ignoré
    })
    assert args["nom"] == "BBB", args
    assert args["objet"] == "Livre ancien avec des symboles nécromantiques", args
    assert args["quantite"] == "1", args
    assert "description" not in args, args
    assert any("item" in n for n in notes), notes
    assert any("quantity" in n for n in notes), notes


def test_aliases_n_ecrasent_pas_les_vrais_noms():
    """Un argument au nom CANONIQUE passe sans alias ; l'alias ne s'applique
    que si la résolution normale a échoué."""
    args, notes = sanitize_tool_args(_SpecInv, {"objet": "corde", "nom": "BBB"})
    assert args == {"objet": "corde", "nom": "BBB"}, args
    assert notes == [], notes
