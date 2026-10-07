# -*- coding: utf-8 -*-
"""Déplace le calcul de `mots` avant les branches de routage."""
p = r"F:\projets\d&d app\server\llm\orchestrator.py"
src = open(p, encoding="utf-8").read()

# 1. supprime la définition tardive de `mots`
tardif = ("    # Sinon : meilleur article du catalogue par mots communs (singuliers des\n"
          "    # DEUX côtés — « flèches » demandé vs « Flèches (10) » en catalogue).\n"
          "    mots = {_sing_mot(w) for w in _norm(msg).split() if len(w) >= 4}\n")
assert tardif in src, "bloc tardif introuvable"
src = src.replace(tardif, "    # Sinon : meilleur article du catalogue par mots communs.\n")

# 2. insère le calcul tôt (avant les branches repas/vente)
totot = "    # Repas / logement → auberge_commander.\n"
assert totot in src
src = src.replace(
    totot,
    "    mots = {_sing_mot(w) for w in _norm(msg).split() if len(w) >= 4}\n\n"
    + totot)

open(p, "w", encoding="utf-8").write(src)
import ast
ast.parse(src)
print("déplacé + compile")
