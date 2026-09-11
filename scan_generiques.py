import json
import re
from pathlib import Path

racine = Path("server/data/scenarios")
occurrences: dict[str, set] = {}
for f in racine.rglob("*.json"):
    try:
        s = f.read_text(encoding="utf-8")
    except Exception:
        continue
    hits = set(re.findall(r"[A-Za-zÀ-ÿ_]+_de_taille_[A-Za-zÀ-ÿ]+", s))
    if hits:
        occurrences[str(f.relative_to(racine))] = hits

print("scénarios référençant des gabarits génériques:")
for f, hits in occurrences.items():
    print(" -", f, "->", sorted(hits)[:6])
