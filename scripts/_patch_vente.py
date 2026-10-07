# -*- coding: utf-8 -*-
"""Ajoute la route VENTE au rattrapage (type='vente' → marche_vendre)."""
p = r"F:\projets\d&d app\server\llm\orchestrator.py"
src = open(p, encoding="utf-8").read()
ancien = '''                elif _achat.get("type") == "marche":
                    _tr_ach = await self.execute_tool_direct(
                        "marche_acheter",
                        {"nom": _pj_nom_achat,
                         "article": _achat.get("article", ""),
                         "quantite": int(_achat.get("quantite") or 1)},
                        ctx, on_event, result,
                    )'''
nouveau = ancien + '''
                elif _achat.get("type") == "vente":
                    # 💰 Partie 2dfa9c75 : la revente crédite l'or du PJ à
                    # 50 % du prix de base (règle officielle) via le tool.
                    _tr_ach = await self.execute_tool_direct(
                        "marche_vendre",
                        {"nom": _pj_nom_achat,
                         "article": _achat.get("article", ""),
                         "quantite": int(_achat.get("quantite") or 1)},
                        ctx, on_event, result,
                    )'''
assert ancien in src, "bloc achat introuvable"
src = src.replace(ancien, nouveau)
open(p, "w", encoding="utf-8").write(src)
print("route vente ajoutée")
