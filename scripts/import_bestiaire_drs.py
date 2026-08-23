# -*- coding: utf-8 -*-
"""Importe les fiches monstres du corpus DRS (règles officielles 3.5 VF,
regles-donjons-dragons.com) dans le bestiaire local `server/data/bestiaire.json`.

Le corpus est découpé en pages `===== page NNNN - Titre =====` ; une page est
une fiche monstre si elle contient à la fois `**Facteur de puissance ...**` et
`**Classe d'armure ...**`. Chaque fiche est convertie au format du bestiaire
(mêmes champs que monstre_ajouter_bestiaire) puis fusionnée — les monstres
déjà présents (entrées écrites à la main) sont conservés tels quels.

Usage :
    python import_bestiaire_drs.py <corpus_dir> <bestiaire.json> [--dry-run]
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def slug(texte: str) -> str:
    nf = unicodedata.normalize("NFKD", texte)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:60].strip("_").lower() or "monstre"


def norm(s: str) -> str:
    """Minuscule sans accents ni ponctuation multiple, pour comparaisons."""
    nf = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in nf if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def esp(s: str) -> str:
    """Normalise espaces / retours ligne multiples en un seul espace."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    # Moins mathématique U+2212 → ASCII (cohérence avec le bestiaire existant).
    return s.replace("\u2212", "-")


def _vide(s: str) -> bool:
    return not s or s.strip() in {"-", "—", "\u2014", "\u2013"}


# --------------------------------------------------------------------------- #
#  Découpage en pages
# --------------------------------------------------------------------------- #
# Le séparateur « page NNNN - Titre » peut utiliser un tiret ASCII, court ou
# long selon les fichiers du corpus.
_PAGE_RE = re.compile(r"^===== page (\d+)\s*[-\u2013\u2014]\s*(.*?) =====\s*$", re.M)


def iter_pages(texte: str):
    """Renvoie (n° page, titre, contenu) pour chaque page du fichier."""
    marks = list(_PAGE_RE.finditer(texte))
    for i, m in enumerate(marks):
        fin = marks[i + 1].start() if i + 1 < len(marks) else len(texte)
        yield int(m.group(1)), m.group(2).strip(), texte[m.end():fin]


# --------------------------------------------------------------------------- #
#  Extraction des champs **Libellé :** valeur
# --------------------------------------------------------------------------- #
_LABEL_RE = re.compile(r"\*\*\s*([^*]+?)\s*:\s*\*\*\s*", re.S)


def parse_champs(contenu: str) -> dict[str, str]:
    """Extrait les couples libellé → valeur des blocs en gras.

    La valeur court jusqu'au prochain marqueur `**` (les libellés sans « : »
    comme le titre de variante restent en dehors des champs).
    """
    parts = _LABEL_RE.split(contenu)
    champs: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        label = esp(parts[i]).rstrip(":").strip()
        val = parts[i + 1].split("**")[0]
        val = esp(val).rstrip(";").strip()
        if label and val and label.lower() not in champs:
            champs[label.lower()] = val
    return champs


# Libellés DRS → champ bestiaire (le premier trouvé gagne).
_CHAMP_ALIASES = {
    "dv": ["dés de vie", "des de vie"],
    "init": ["initiative"],
    "vitesse": ["vitesse de déplacement", "vitesse"],
    "ca": ["classe d'armure"],
    "bab": ["attaque de base/lutte"],
    "attaques": ["attaque", "attaques"],
    "degs": ["attaque à outrance", "attaque a outrance"],
    "speciales": ["attaques spéciales", "attaques speciales"],
    "particularites": ["particularités", "particularites"],
    "sauvegardes": ["jets de sauvegarde"],
    "carac": ["caractéristiques", "caracteristiques"],
    "comp": ["compétences", "competences"],
    "dons": ["dons"],
    "fp": ["facteur de puissance"],
    "alignement": ["alignement"],
    "environnement": ["environnement"],
}


def champ(champs: dict[str, str], cle: str) -> str:
    for alias in _CHAMP_ALIASES[cle]:
        if alias in champs:
            return champs[alias]
    return ""


# « Humanoïde (gobelinoïde) de taille P » → type + taille
_TYPE_RE = re.compile(r"^(.*?)\s+de taille\s+([TPMGC]{1,2}|Très\s+\w+|Min\.?)\b", re.I)
_PV_RE = re.compile(r"\((\d+)\s*pv\)")


def fiche_depuis_page(titre_page: str, contenu: str) -> dict | None:
    """Construit une entrée bestiaire depuis une page, ou None si pas une fiche."""
    # Normalisation typographique : le corpus DRS utilise l'apostrophe
    # typographique U+2019 (Classe d'armure) et des espaces insécables.
    contenu = contenu.replace("\u2019", "'").replace("\u00a0", " ")

    # Filtre : une fiche monstre a TOUJOURS FP + CA + DV en gras.
    if "**Facteur de puissance" not in contenu or "**Classe d'armure" not in contenu:
        return None
    if "**Dés de vie" not in contenu and "**Des de vie" not in contenu:
        return None

    champs = parse_champs(contenu)
    dv = champ(champs, "dv")
    fp = champ(champs, "fp")
    ca = champ(champs, "ca")
    if not dv or not fp:
        return None

    # Nom : le titre de la page (toujours fiable) ou le 1er gras non-étiquette
    # dans le bloc d'ouverture.
    nom = esp(titre_page)
    m_title = re.search(r"^##\s+.+$", contenu, re.M)
    if m_title:
        apres = contenu[m_title.end():m_title.end() + 600]
        spans = re.findall(r"\*\*([^*]+?)\*\*", apres)
        for sp in spans:
            sp = esp(sp)
            if not sp or ":" in sp:
                continue
            if re.search(r"\b(?:For|Dex|Con|Int|Sag|Cha|Str|Wis)\s+\d", sp):
                continue
            if "de taille" in sp.lower():
                continue
            nom = sp
            break
    # Écarte les « titres » qui sont en fait des libellés de bloc.
    if nom.lower().startswith(("dés de vie", "initiative", "vitesse")):
        nom = esp(titre_page)

    # Type / taille depuis la ligne de sous-titre (2e gras sans « : »).
    type_monstre, taille = "", ""
    blocs = re.findall(r"\*\*([^*:+]+?)\*\*", contenu[:1500])
    for b in blocs[1:]:
        m = _TYPE_RE.match(esp(b))
        if m:
            type_monstre = esp(m.group(1))
            taille = esp(m.group(2)).upper()[:1]
            if not taille or taille not in "TPMG":
                taille = esp(m.group(2))
            break
    if not type_monstre:
        type_monstre = "—"

    m_pv = _PV_RE.search(dv)
    pv = int(m_pv.group(1)) if m_pv else 0

    capacites = "; ".join(x for x in (
        f"Attaques spéciales : {champ(champs, 'speciales')}" if not _vide(champ(champs, "speciales")) else "",
        f"Particularités : {champ(champs, 'particularites')}" if not _vide(champ(champs, "particularites")) else "",
    ) if x)

    cle = slug(nom)
    return {
        "nom": nom,
        "type": type_monstre,
        "taille": taille,
        "dv": dv,
        "pv": pv,
        "ca": 0,  # rempli après coup depuis _ca_texte
        "vitesse": champ(champs, "vitesse"),
        "bab": "",  # rempli après coup depuis _bab_texte
        "init": champ(champs, "init"),
        "attaques": champ(champs, "attaques"),
        "degs": champ(champs, "degs") or champ(champs, "attaques"),
        "sauvegardes": champ(champs, "sauvegardes"),
        "carac": champ(champs, "carac"),
        "comp": champ(champs, "comp"),
        "dons": champ(champs, "dons"),
        "capacites": capacites or "—",
        "faiblesses": "—",
        "fp": fp,
        "alignement": champ(champs, "alignement") or "—",
        "cle": cle,
        "source": "DRS 3.5 (regles-donjons-dragons.com)",
        "prompt_image": f"fantasy {nom.lower()} creature, D&D 3.5 illustration, ink style, dramatic lighting",
        "_ca_texte": champ(champs, "ca"),
        "_bab_texte": champ(champs, "bab"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_dir", help="dossier knowledge_import (ou KB4_DRS_corpus)")
    ap.add_argument("bestiaire", help="chemin du bestiaire.json à mettre à jour")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    racine = Path(args.corpus_dir)
    # Accepte la racine knowledge_import comme un sous-dossier KB* direct.
    fichiers = []
    for candidat in [racine, racine / "KB4_DRS_corpus"]:
        fichiers = sorted(candidat.glob("*.txt")) if candidat.is_dir() else []
        if fichiers:
            break
    if not fichiers:
        print(f"❌ Aucun .txt trouvé sous {racine}")
        return 1

    fiches: list[dict] = []
    vus: set[str] = set()
    for f in fichiers:
        texte = f.read_text(encoding="utf-8")
        for num, titre, contenu in iter_pages(texte):
            fiche = fiche_depuis_page(titre, contenu)
            if not fiche:
                continue
            if fiche["cle"] in vus:
                continue
            vus.add(fiche["cle"])
            # Champs dérivés : CA numérique depuis le texte, BBA depuis
            # « Attaque de base/lutte : +6/+14 » (premier nombre).
            m = re.match(r"(\d+)", fiche.pop("_ca_texte"))
            fiche["ca"] = int(m.group(1)) if m else 0
            b = fiche.pop("_bab_texte")
            mb = re.search(r"[+-]?\d+", b)
            fiche["bab"] = mb.group(0) if mb else ""
            fiches.append(fiche)

    # Fusion dans le bestiaire existant (les entrées manuelles gagnent).
    chemin = Path(args.bestiaire)
    bestiaire: dict = {}
    if chemin.is_file():
        bestiaire = json.loads(chemin.read_text(encoding="utf-8"))
    existants = {norm(k) for k in bestiaire if k != "_meta"}
    # aussi par nom normalisé (une entrée manuelle « Gobelin » bloque l'ajout
    # DRS d'une fiche homonyme mais pas d'une variante « Gobelin, cavalier worg »)
    noms_existants = {norm(v.get("nom", "")) for v in bestiaire.values()
                      if isinstance(v, dict)}

    ajoutes, ignores = [], []
    for fiche in fiches:
        if fiche["cle"] in existants or norm(fiche["nom"]) in noms_existants:
            ignores.append(fiche["nom"])
            continue
        bestiaire[fiche["cle"]] = fiche
        existants.add(fiche["cle"])
        ajoutes.append(fiche["nom"])

    nb = sum(1 for k, v in bestiaire.items()
             if k != "_meta" and isinstance(v, dict) and "nom" in v)
    bestiaire.setdefault("_meta", {})["nb_monstres"] = nb
    bestiaire["_meta"]["maj_drs"] = "import auto depuis corpus DRS"

    print(f"Fiches DRS détectées : {len(fiches)} | ajoutées : {len(ajoutes)} | "
          f"doublons ignorés : {len(ignores)} | total bestiaire : {nb}")
    if args.dry_run:
        for n in ajoutes[:40]:
            print(f"  + {n}")
        return 0

    chemin.write_text(
        json.dumps(bestiaire, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK Bestiaire ecrit : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
