"""État persistant d'une partie D&D 3.5 — réutilise le schéma de
`Outil_EtatPartie_MEMOIRE.py` (format `data/partie_<id>.json`).

Cette couche est partagée entre :
- les tools (mémoire, fiches, carte) qui lisent/écrivent l'état,
- le filtre d'injection (qui envoie un récap au LLM),
- le frontend (qui expose initiative/PV/lieu via API REST).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# --------------------------------------------------------------------------- #
#  Schéma par défaut d'une nouvelle partie (identique à l'original)
# --------------------------------------------------------------------------- #
SCHEMA_PARTIE: dict[str, Any] = {
    "meta": {
        "titre": "(en attente)",
        "cadre": "Côte des Épées (Faerûn)",
        "regles": "D&D 3.5",
        "date_creation": "",
        "date_maj": "",
    },
    "phase": "opening",
    "tour": 0,
    "courant_tour_pour": None,
    "tour_depuis": None,
    "initiative": [],
    "pj": [],
    "pnj": [],
    "lieu": {"nom": "(non déterminé)", "type": "ville", "description": "",
             "position_x": 0, "position_y": 0},
    "donjon": {"id": None, "salles_visitees": [], "portes_bloquees": [], "grille": [],
               "etage": 0, "etages": {}},
    "donjons_exploreres": {},
    "quete": {"titre": "", "pitch": "", "source": ""},
    "histoire": [],
    "derniere_narration": "",
    # Calepin du MJ : notes libres devant la table, avec cases à cocher.
    # Chaque entrée : {"id": str, "texte": str, "fait": bool}.
    "calepin": [],
    # Journal des illustrations de monstres croisés ({nom, url}) — sert à
    # réhydrater la galerie « Monstres rencontrés » après rechargement.
    "rencontres_images": [],
    # Mémoire de campagne longue (cohérence) : missions, lieux, PNJ,
    # monstres combattus (rempli par le moteur de combat), position.
    # `intrigue_resume` (résumé continu), `objectif_courant` (ce qu'il faut
    # faire maintenant) et `evenements_rencents` (journal récent) gardent le
    # fil de l'histoire même quand l'historique chat est tronqué.
    "memoire": {
        "missions": [],
        "lieux_visites": [],
        "personnages_rencontres": [],
        "monstres_combattus": [],
        "position": {"lieu": "", "zone": "", "detail": ""},
        "intrigue_resume": "",
        "objectif_courant": "",
        "evenements_rencents": [],
    },
}


class PartyState:
    """Encapsule lecture/écriture de l'état d'une partie sur disque.

    Toutes les écritures sont atomiques (tempfile + os.replace) pour éviter la
    corruption en cas de crash.
    """

    def __init__(self, data_dir: str, partie_id: str, max_history: int = 50):
        self.data_dir = Path(data_dir)
        self.partie_id = partie_id
        self.max_history = max_history
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.data_dir / f"partie_{self.partie_id}.json"

    # ------------------------------------------------------------------ #
    def load(self) -> dict[str, Any]:
        """Charge l'état ; renvoie un état neuf si absent."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            etat = copy.deepcopy(SCHEMA_PARTIE)
            etat["meta"]["date_creation"] = datetime.now().isoformat()
            return etat
        except (json.JSONDecodeError, OSError) as e:
            return {"_erreur": str(e)}

    def save(self, etat: dict[str, Any]) -> Optional[str]:
        """Écriture atomique. Renvoie un message d'erreur ou None si ok."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        etat.setdefault("meta", {})["date_maj"] = datetime.now().isoformat()
        fd, tmp = tempfile.mkstemp(
            dir=str(self.data_dir), prefix=".partie_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(etat, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return f"❌ Erreur écriture : {e}"
        return None

    # ------------------------------------------------------------------ #
    #  Patch stratégie par points (ex. "pj.0.pv" -> 12)
    # ------------------------------------------------------------------ #
    def patch(self, chemin: str, valeur_json: str) -> tuple[bool, str]:
        """Patche une clé hiérarchique.

        - `valeur_json` est interprété comme JSON si possible (int, bool, list,
          dict), sinon texte brut.
        - Gère les listes indexées : quand un parent est une liste (ex. `pj`
          dans le schéma), la clé suivante est lue comme un entier et le
          tableau est auto-étendu si besoin. C'est nécessaire car le LLM écrit
          naturellement `pj.0.nom`, `pj.0.pv`, etc. — sans ce traitement, le
          patch produisait `pj = {"0": {"nom": ...}}` au lieu d'une liste.
        - Renvoie (succès, message).
        """
        etat = self.load()
        if "_erreur" in etat:
            return False, f"❌ Impossible de charger l'état : {etat['_erreur']}"

        try:
            v: Any = json.loads(valeur_json)
        except json.JSONDecodeError:
            v = valeur_json

        keys = chemin.split(".")
        cur: Any = etat
        # On descend en tenant compte de la clé suivante pour savoir s'il faut
        # créer un dict ou une liste quand la valeur est absente.
        for i, k in enumerate(keys[:-1]):
            nxt = keys[i + 1]
            cur = self._descend(cur, k, nxt)
        # Dernier segment : on écrit la valeur.
        self._set_child(cur, keys[-1], v)

        err = self.save(etat)
        if err:
            return False, err
        return True, f"✅ Mis à jour : {chemin} = {json.dumps(v, ensure_ascii=False)}"

    # ------------------------------------------------------------------ #
    def _descend(self, cur: Any, key: str, next_key: str) -> Any:
        """Descend d'un niveau dans `cur` selon sa nature (dict ou list).

        `next_key` détermine la forme du conteneur à créer quand `key` est
        absent : une liste si `next_key` est un entier, un dict sinon.
        """
        if isinstance(cur, dict):
            cur_val = cur.get(key, None)
            # Conteneur déjà existant : on y descend tel quel.
            if isinstance(cur_val, (dict, list)):
                return cur_val
            # Valeur absente ou scalaire : on crée selon la nature attendue.
            if next_key.lstrip("-").isdigit():
                cur[key] = []
            else:
                cur[key] = {}
            return cur[key]
        if isinstance(cur, list):
            idx = self._as_index(key, cur)
            elem = cur[idx]
            # L'élément par défaut créé par _as_index est un dict vide ; si la
            # descente suivante attend une liste (next_key entier), on convertit.
            if next_key.lstrip("-").isdigit() and not isinstance(elem, list):
                cur[idx] = []
                return cur[idx]
            return elem
        # cur scalaire : on ne peut pas descendre (best effort).
        return cur

    @staticmethod
    def _as_index(key: str, container: list) -> int:
        """Convertit key en index entier et auto-étend la liste avec des
        dicts vides jusqu'à cet index inclus. Évite IndexError et garantit
        que pj.0.* puis pj.1.* créent bien [ {...}, {...} ].
        """
        try:
            idx = int(key)
        except (TypeError, ValueError):
            idx = 0
        if idx < 0:
            idx = max(0, len(container) + idx)
        while len(container) <= idx:
            container.append({})  # élément par défaut = dict vide (PJ/PNJ)
        return idx

    @staticmethod
    def _set_child(cur: Any, key: str, value: Any) -> None:
        """Écrit `value` à la clé/index `key` dans `cur` (dict ou list)."""
        if isinstance(cur, dict):
            cur[key] = value
            return
        if isinstance(cur, list):
            idx = PartyState._as_index(key, cur)
            cur[idx] = value
            return
        # cur scalaire : rien à faire (best effort).

    def replace_all(self, nouveau_etat: Any) -> tuple[bool, str]:
        """Remplace l'état complet. Préserve la date de création si présente."""
        # Accepte une chaîne JSON (format tool `etat_partie_save`) ou un dict.
        # NB : l'ancien garde-fou `isinstance(nouveau_etat, dict)` rejetait
        # TOUTE chaîne JSON — le tool ne pouvait donc jamais aboutir.
        try:
            data = json.loads(nouveau_etat) if isinstance(nouveau_etat, str) else nouveau_etat
        except json.JSONDecodeError as e:
            return False, f"❌ JSON invalide : {e}"
        if not isinstance(data, dict):
            return False, "❌ Attendu un objet JSON à la racine."
        ancien = self.load()
        if isinstance(ancien, dict) and ancien.get("meta", {}).get("date_creation"):
            data.setdefault("meta", {})["date_creation"] = ancien["meta"]["date_creation"]
        err = self.save(data)
        if err:
            return False, err
        return True, "✅ État de partie sauvegardé."

    def reset(self) -> str:
        """Supprime l'état persistant (nouvelle partie confirmée)."""
        try:
            self.path.unlink()
            return f"✅ Partie réinitialisée (fichier {self.path.name} supprimé)."
        except FileNotFoundError:
            return "ℹ️ Aucun état à réinitialiser."
        except OSError as e:
            return f"❌ Erreur : {e}"

    # ------------------------------------------------------------------ #
    def add_event(self, evenement: str, tour: str = "") -> str:
        etat = self.load()
        if not isinstance(etat, dict):
            return f"❌ État illisible : {etat}"
        entree = {
            "ts": datetime.now().isoformat(),
            "tour": tour or "",
            "evenement": evenement,
        }
        etat.setdefault("histoire", []).append(entree)
        if len(etat["histoire"]) > self.max_history:
            etat["histoire"] = etat["histoire"][-self.max_history:]
        err = self.save(etat)
        return err or f"✅ Événement ajouté au journal : « {evenement} »"

    # ------------------------------------------------------------------ #
    #  Calepin (journal de notes du MJ)
    # ------------------------------------------------------------------ #
    def calepin_lire(self) -> list[dict[str, Any]]:
        etat = self.load()
        if not isinstance(etat, dict):
            return []
        return list(etat.get("calepin") or [])

    def calepin_ajouter(self, texte: str, fait: bool = False) -> tuple[Optional[str], str]:
        """Ajoute une note. Renvoie (erreur, id)."""
        etat = self.load()
        if not isinstance(etat, dict):
            return "État illisible", ""
        from uuid import uuid4
        note_id = uuid4().hex[:8]
        etat.setdefault("calepin", []).append({
            "id": note_id, "texte": texte.strip()[:500], "fait": bool(fait),
        })
        err = self.save(etat)
        return err, note_id

    def calepin_maj(self, note_id: str, texte: Optional[str] = None,
                    fait: Optional[bool] = None) -> Optional[str]:
        """Met à jour une note (texte et/ou case cochée). Renvoie erreur ou None."""
        etat = self.load()
        if not isinstance(etat, dict):
            return "État illisible"
        notes = etat.get("calepin") or []
        for n in notes:
            if n.get("id") == note_id:
                if texte is not None:
                    n["texte"] = texte.strip()[:500]
                if fait is not None:
                    n["fait"] = bool(fait)
                return self.save(etat)
        return "Note introuvable"

    def calepin_supprimer(self, note_id: str) -> Optional[str]:
        etat = self.load()
        if not isinstance(etat, dict):
            return "État illisible"
        notes = etat.get("calepin") or []
        avant = len(notes)
        etat["calepin"] = [n for n in notes if n.get("id") != note_id]
        if len(etat["calepin"]) == avant:
            return "Note introuvable"
        return self.save(etat)

    def set_derniere_narration(self, narration: str) -> str:
        etat = self.load()
        if not isinstance(etat, dict):
            return f"❌ État illisible : {etat}"
        etat["derniere_narration"] = narration[:1500]
        err = self.save(etat)
        return err or "✅ Dernière narration mémorisée."
