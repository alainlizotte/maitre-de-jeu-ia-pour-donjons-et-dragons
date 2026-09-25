# -*- coding: utf-8 -*-
"""Régénère le portrait d'un PJ avec le prompt corrigé (variante sans arme).
Usage : python _regen_portrait.py <nom> <proprietaire>"""
import asyncio
import io
import json
import os
import sys

sys.path.insert(0, "/app")

nom = sys.argv[1] if len(sys.argv) > 1 else "bozo"
proprietaire = sys.argv[2] if len(sys.argv) > 2 else "alain"
data_dir = "/app/server/data"

# Fiche du compte (recherche par slug, propriétaire insensible à la casse)
from server.persos import (  # noqa: E402
    _slug, generer_portrait_async, construire_prompt_portrait,
)

fiche = None
for p in os.listdir(os.path.join(data_dir, "fiches")):
    if not p.endswith(".json"):
        continue
    try:
        with io.open(os.path.join(data_dir, "fiches", p), encoding="utf-8") as fh:
            cand = json.load(fh)
    except Exception:
        continue
    if (_slug(str(cand.get("nom", ""))) == _slug(nom)
            and str(cand.get("proprietaire", "")).lower() == proprietaire.lower()):
        fiche = cand
        break

if fiche is None:
    print(f"❌ Fiche introuvable pour {nom}/{proprietaire}")
    sys.exit(1)

cache_dir = os.path.join(data_dir, "portraits_cache")
for base in (f"perso_{_slug(proprietaire)}_{_slug(nom)}.png", f"{_slug(nom)}.png"):
    p = os.path.join(cache_dir, base)
    if os.path.isfile(p):
        os.remove(p)
        print(f"🗑️ cache retiré : {base}")

print("PROMPT:", construire_prompt_portrait(fiche)[:220])
ecrit = asyncio.run(generer_portrait_async(data_dir, fiche))
print("PORTRAIT:", ecrit or "❌ génération échouée")
