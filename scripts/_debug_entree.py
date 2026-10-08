# -*- coding: utf-8 -*-
"""Quel motif d'ENTRÉE a matché la narration EXACTE de c21d0734 ?"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.main import _DONJON_LIEU_RE, _DONJON_ENTREE_RE  # noqa: E402

with open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "server", "data", "chat_c21d0734.json"), encoding="utf-8") as f:
    chat = json.load(f)
narr = chat[5]["content"]

m_l = _DONJON_LIEU_RE.search(narr)
print("LIEU →", repr(m_l.group(0)) if m_l else None)
for m in _DONJON_ENTREE_RE.finditer(narr):
    print("ENTRÉE →", repr(narr[max(0, m.start()-80):m.end()+40]))
