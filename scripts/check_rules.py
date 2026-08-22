# -*- coding: utf-8 -*-
"""Analyseur de conformité D&D 3.5 — lit /tmp/sim_state.json + l'état de la partie
et vérifie l'application des règles officielles."""
import json
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
DICE_TOOLS = {"lancer_attaque", "lancer_degats", "lancer_d20", "lancer_sauvegarde",
              "calculer_initiative", "demarrer_combat", "fiche_perso_infliger_degats"}

issues = []
oks = []


def check(cond, ok_msg, ko_msg):
    if cond:
        oks.append(ok_msg)
    else:
        issues.append(ko_msg)


def main():
    with open("/tmp/sim_state.json", encoding="utf-8") as f:
        st = json.load(f)
    pid = st["party_id"]

    # --- 1. État de la partie ---
    with urllib.request.urlopen(f"{BASE}/api/parties/{pid}") as r:
        etat = json.load(r)["etat"]

    print("=" * 70)
    print(f"PARTIE {pid} — phase={etat.get('phase')} | quête={etat['quete'].get('titre')!r}")
    print(f"PJ: {[(p['nom'], p['pv'], p['pv_max'], p['ca']) for p in etat['pj']]}")
    print(f"initiative: {len(etat.get('initiative') or [])} | histoire: {len(etat.get('histoire') or [])}")

    # --- 2. Fiches : vérif règles de création ---
    print("\n--- FICHES (règles de création 3.5) ---")
    for pj in etat["pj"]:
        nom = pj["nom"]
        from urllib.parse import quote
        with urllib.request.urlopen(
            f"{BASE}/api/fiches/{quote(nom)}?partie_id={pid}"
        ) as r:
            fiche = json.load(r)
        f = fiche.get("fiche", fiche)
        caracs = f.get("carac") or {}
        mods = {k: (v - 10) // 2 for k, v in caracs.items()}
        # CA = 10 + mod DEX (sans armure structurée)
        ca_attendue = 10 + mods.get("DEX", 0)
        check(pj["ca"] >= ca_attendue,
              f"{nom}: CA {pj['ca']} >= 10+Dex({ca_attendue})",
              f"{nom}: CA {pj['ca']} < minimum {ca_attendue} (mod Dex ignoré ?)")
        # PV niveau 1 = max dé vie + mod CON
        dv = {"Guerrier": 10, "Magicien": 4, "Voleur": 6, "Roublard": 6, "Rogue": 6,
              "Clerc": 8, "Druide": 8, "Barde": 6, "Paladin": 10, "Ranger": 8,
              "Moine": 8, "Sorcier": 4, "Barbare": 12}.get(str(pj.get("classe", "")), None)
        if dv:
            pv_attendus = dv + mods.get("CON", 0)
            check(pj["pv_max"] == pv_attendus,
                  f"{nom}: PV {pj['pv_max']} = max(d{dv})+CON {pv_attendus}",
                  f"{nom}: PV {pj['pv_max']} ≠ max(d{dv})+CON = {pv_attendus}")
        # caracs dans plage légale 3..20 après mods raciaux
        hors = {k: v for k, v in caracs.items() if not (3 <= v <= 20)}
        check(not hors, f"{nom}: caracs toutes en 3-20", f"{nom}: caracs hors plage {hors}")

    # --- 3. Jets de dés dans le transcript ---
    print("\n--- JETS DE DÉS (transcript) ---")
    n_attack = n_degats = n_d20 = 0
    simu_en_prose = []
    for e in st["transcript"]:
        trace = e.get("tool_calls_trace", [])
        tool_names = [tc.get("name") for tc in trace]
        has_dice = any(n in DICE_TOOLS for n in tool_names)
        for tc in trace:
            n = tc.get("name", "")
            args = tc.get("args", tc.get("arguments", {})) or {}
            res = str(tc.get("text", tc.get("result", "")))
            if n == "lancer_attaque":
                n_attack += 1
                m = re.search(r"Total attaque : (\d+)", res)
                b = args.get("bonus_attaque")
                if m and b is not None:
                    total, brut = int(m.group(1)), None
                    mb = re.search(r"Jet brut d'attaque : (\d+)", res)
                    if mb:
                        brut = int(mb.group(1))
                        check(total == brut + int(b) or "ajusté" in res,
                              f"attaque #{n_attack}: {brut}+{b}={total} cohérent",
                              f"attaque #{n_attack}: total {total} ≠ {brut}+{b}")
            elif n == "lancer_degats":
                n_degats += 1
            elif n == "lancer_d20":
                n_d20 += 1
            elif n == "demarrer_combat":
                check(True, "demarrer_combat appelé", "")
        # dégâts narrés sans tool dans le tour
        for m in re.finditer(r"\*{0,2}(\d+)\s*(?:points de\s+)?d[ée]g[âa]ts", e.get("dm_text", ""), re.IGNORECASE):
            if not has_dice:
                simu_en_prose.append((e["label"], m.group(0)))

    print(f"lancer_attaque: {n_attack} | lancer_degats: {n_degats} | lancer_d20: {n_d20}")
    if simu_en_prose:
        for lbl, s in simu_en_prose[:10]:
            issues.append(f"dégâts narrés sans tool ({lbl}): …{s}…")
    else:
        oks.append("aucun dégât narré à la main dans les tours avec tools")

    # --- 4. Bilan ---
    print("\n" + "=" * 70)
    print(f"CONFORME: {len(oks)} points OK")
    for o in oks:
        print(f"  ✅ {o}")
    if issues:
        print(f"\nNON-CONFORMES: {len(issues)} problèmes")
        for i in issues:
            print(f"  ❌ {i}")
        sys.exit(1)
    print("\nTOUT EST CONFORME")


if __name__ == "__main__":
    main()
