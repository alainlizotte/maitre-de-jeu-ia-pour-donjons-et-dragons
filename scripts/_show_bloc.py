# -*- coding: utf-8 -*-
"""Affiche le bloc de routage achat/vente du rattrapage."""
src = open(r"F:\projets\d&d app\server\llm\orchestrator.py",
           encoding="utf-8").read()
i = src.find('elif _achat.get("type") == "marche":')
print(src[i:i+520])
