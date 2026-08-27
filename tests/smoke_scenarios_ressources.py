"""Smoke test : catalogue de scénarios structuré par univers et
extraction PDF à la demande. À exécuter dans l'image Docker (PyMuPDF) :

    docker run --rm -v "<projet>:/app" dnd35-mj:latest python tests/smoke_scenarios_ressources.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext  # noqa: E402
from server.tools.scenarios import (  # noqa: E402
    charger_catalogue,
    scenarios_laelith_charger,
    scenarios_laelith_lister,
)

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "data")


async def main() -> None:
    ctx = ToolContext(partie_id="test", joueur="joueur 1", data_dir=DATA)

    # 1. Catalogue structuré chargé correctement
    cata = charger_catalogue(ctx)
    universes = cata.get("universes", [])
    assert len(universes) == 4, f"4 univers attendus, trouvé {len(universes)}"
    n_total = sum(len(u.get("scenarios", [])) for u in universes)
    assert n_total == 24, f"24 scénarios attendus, trouvé {n_total}"
    print("✅ catalogue : 4 univers, %d scénarios" % n_total)

    # 2. Lister (tool LLM) — formattage par univers
    l = await scenarios_laelith_lister(ctx)
    assert "Divers" in l.text
    assert "Laelith" in l.text
    assert "Royaumes" in l.text
    assert "Terres" in l.text
    print("✅ lister : les 4 univers apparaissent")

    # 3. Charger un scénario PDF (Army of the Damned, assets multiples)
    r = await scenarios_laelith_charger(ctx, "divers_army_of_the_damned")
    assert "Army of the Damned" in r.text
    assert "/data/scenarios/" in r.text
    assert "TEXTE DU SCÉNARIO" in r.text
    assert "Cartes" in r.text or "cartes" in r.text
    print("✅ charger Army : texte extrait + cartes listed")

    # 4. Charger un scénario Laelith (avec annexes monstresList.htm)
    r2 = await scenarios_laelith_charger(ctx, "laelith_loeil_de_gruumsh")
    assert "Gruumsh" in r2.text
    assert "Annexes" in r2.text or "annexes" in r2.text
    print("✅ charger Oeil de Gruumsh : texte + annexes")

    # 5. Charger un scénario Royaumes Oubliés
    r3 = await scenarios_laelith_charger(ctx, "ro_to_find_a_gate")
    assert "To Find a Gate" in r3.text
    assert "Cartes" in r3.text or "cartes" in r3.text
    print("✅ charger To Find a Gate : texte + cartes")

    # 6. Dragon de Hurlemont (Terres de l'Eternel, avec annexes fiches Joueurs)
    r4 = await scenarios_laelith_charger(ctx, "terres_dragon_hurlemont")
    assert "Dragon de Hurlemont" in r4.text
    assert "Annexes" in r4.text or "annexes" in r4.text
    print("✅ charger Dragon de Hurlemont : texte + annexes fiches Joueurs")

    # 7. ID inconnu
    r5 = await scenarios_laelith_charger(ctx, "ZZ")
    assert "introuvable" in r5.text
    print("✅ ID inconnu → message d'erreur")

    # 8. Vérifier JSON catalogue valide
    cat_path = os.path.join(DATA, "scenarios_catalogue.json")
    with open(cat_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "universes" in data
    assert len(data["universes"]) == 4
    print("✅ JSON catalogue valide sur disque")

    print("\n🎉 tous les tests scénarios passent")


if __name__ == "__main__":
    asyncio.run(main())
