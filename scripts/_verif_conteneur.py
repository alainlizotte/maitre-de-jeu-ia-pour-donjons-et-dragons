# -*- coding: utf-8 -*-
"""Vérifie que le conteneur tourne sur le dernier code + config + données."""
import subprocess
import sys

MARKERS = [
    ("butin de salle (orchestrator)",
     "/app/server/llm/orchestrator.py", "_butin_salle_courante", 2),
    ("route vente (rattrapage)",
     "/app/server/llm/orchestrator.py", '== "vente"', 1),
    ("intention vente",
     "/app/server/llm/orchestrator.py", "je\\s+vends", 1),
    ("or narré",
     "/app/server/llm/orchestrator.py", "_or_gagne_narre", 2),
    ("roll-to-confirm jets",
     "/app/server/llm/orchestrator.py", "roll-to-confirm", 1),
    ("pronoms PJ",
     "/app/server/llm/orchestrator.py", "_corriger_pronoms_pj", 2),
    ("anti-triche salle",
     "/app/server/tools/inventaire.py", "ne peut pas être gagné ICI", 1),
    ("sanitation auberge",
     "/app/server/tools/marche.py", "not isinstance(repas, str)", 1),
    ("soigner un MORT refusé",
     "/app/server/tools/fiches.py", "est MORT", 1),
    ("alias variante (monstres)",
     "/app/server/tools/monstres.py", "_ALIASES_VARIANTE", 2),
    ("mapping vente (prompt)",
     "/app/server/prompts/SystemPrompt_EXPLORATION_COURT.md",
     "marche_vendre", 1),
    ("mapping achat (prompt)",
     "/app/server/prompts/SystemPrompt_EXPLORATION_COURT.md",
     "marche_acheter", 1),
    ("garde intro décor (prompt builder)",
     "/app/server/llm/prompt_builder.py", "_decor_ancre_absent", 2),
]

echecs = []
for label, fichier, motif, attendu in MARKERS:
    r = subprocess.run(
        ["docker", "exec", "dnd35-mj", "grep", "-c", motif, fichier],
        capture_output=True, text=True)
    try:
        n = int((r.stdout or "0").strip().splitlines()[0])
    except ValueError:
        n = 0
    ok = n >= attendu
    print(("✅" if ok else "❌"), label, ":", n, f"(attendu ≥ {attendu})")
    if not ok:
        echecs.append(label)

# Volume + config
r = subprocess.run(
    ["docker", "exec", "dnd35-mj", "grep", "-c", "lycanthrope",
     "/app/server/data/bestiaire.json"], capture_output=True, text=True)
print(("✅" if (r.stdout or "0").strip() != "0" else "❌"),
      "alias bestiaire (volume) :", (r.stdout or "0").strip())

r = subprocess.run(
    ["docker", "exec", "dnd35-mj", "python", "-c",
     "from server.config import get_config; "
     "print(get_config().game.max_history_chars)"],
    capture_output=True, text=True)
print("config max_history_chars :", (r.stdout or "?").strip())

r = subprocess.run(
    ["docker", "exec", "dnd35-mj", "python", "-c",
     "from server.tools.monstres import _find_monstre_strict\n"
     "from server.tools.base import ToolContext\n"
     "ctx = ToolContext(partie_id='t', joueur='t', "
     "data_dir='/app/server/data')\n"
     "m = _find_monstre_strict(ctx, 'Lycanthrope')\n"
     "print('Lycanthrope →', (m or {}).get('nom'))"],
    capture_output=True, text=True)
print((r.stdout or r.stderr).strip()[:120])

print("\nÉCHECS :", echecs or "aucun")
