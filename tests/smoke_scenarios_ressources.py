"""Smoke test : catalogue de scénarios unifié (Laelith + PDF locaux) et
extraction PDF à la demande. À exécuter dans l'image Docker (PyMuPDF) :

    docker run --rm -v "<projet>:/app" dnd35-mj:latest python tests/smoke_scenarios_ressources.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext  # noqa: E402
from server.tools.scenarios import (  # noqa: E402
    scenarios_laelith_charger,
    scenarios_laelith_lister,
)

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "data")


async def main() -> None:
    ctx = ToolContext(partie_id="test", joueur="joueur 1", data_dir=DATA)

    l = await scenarios_laelith_lister(ctx)
    n_pdf = l.text.count("PDF local")
    assert n_pdf == 9, f"9 scénarios PDF attendus, trouvé {n_pdf}"
    print(f"✅ lister : {n_pdf} scénarios PDF locaux listés (+ Laelith)")
    assert "[P9]" in l.text and "[P5]" in l.text

    r = await scenarios_laelith_charger(ctx, "P9")
    assert "Tombeau du Roi nain" in r.text
    assert "/data/scenarios/le-tombeau-du-roi-nain.pdf" in r.text
    assert "TEXTE DU SCÉNARIO" in r.text
    assert len(r.text) > 5000, f"texte extrait trop court : {len(r.text)}"
    print(f"✅ charger P9 : {len(r.text)} caractères de texte extrait")

    r2 = await scenarios_laelith_charger(ctx, "P3")  # fichier avec espaces
    assert "/data/scenarios/Haute%20terrasse" in r2.text
    print("✅ charger P3 : URL encodée correcte")

    r3 = await scenarios_laelith_charger(ctx, "1")  # Laelith conservé
    assert "Voix sous les Pavés" in r3.text
    print("✅ charger 1 : catalogue Laelith conservé")

    r4 = await scenarios_laelith_charger(ctx, "ZZ")
    assert "introuvable" in r4.text
    print("✅ ID inconnu → message d'erreur")

    print("🎉 tous les tests scénarios passent")


if __name__ == "__main__":
    asyncio.run(main())
