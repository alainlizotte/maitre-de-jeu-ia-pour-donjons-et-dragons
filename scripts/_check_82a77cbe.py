# -*- coding: utf-8 -*-
"""Vérification : l'intro de 82a77cbe ne doit plus extraire couronne/baguette."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.llm.orchestrator import _objets_remettes_narration  # noqa: E402

with open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))),
        "server", "data", "chat_82a77cbe.json"), encoding="utf-8") as f:
    chat = json.load(f)
a = chat[1]["content"].split("🎁")[0]
print("extraits maintenant :", _objets_remettes_narration(a))
