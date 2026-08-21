"""Intégration ComfyUI — génération d'images (monstres, lieux, portraits).

ComfyUI expose une API locale HTTP sur `http://127.0.0.1:8188`. On lui soumet
un workflow JSON via `POST /prompt` puis on interroge `GET /history/{id}`
jusqu'à ce que l'exécution soit terminée. Le PNG produit est alors téléchargé
via `GET /view?filename=...&type=output` et mis en cache localement.

Un seul workflow par usage cible est chargé en mémoire (depuis
`server/image/workflows/<usage>.json`). Le graphe doit être au **format
API** (chaque node a `class_type` et `inputs` — pas le format UI). Pour
chaque génération on identifie par convention deux nodes à patcher :

- `<usage>_PROMPT_NODE` — node `CLIPTextEncode` (ou équivalent) dont le
  champ `text` portera le prompt texte (positif).
- `<usage>_SEED_NODE` — node portant un champ `seed` qu'on randomise pour
  varier les images (peut être absent — on garde alors la seed du workflow).

L'usage d'un template canonique et l'injection de quelques clés permettent
d'éviter de demander au développeur de rewrite le workflow JSON pour chaque
génération : on ne patche que les nodes identifiés.
"""

from __future__ import annotations
import asyncio
import json
import os
import random
import time
from typing import Any, Optional
import httpx

# Usages attendus (mapped à un workflow .json dans server/image/workflows/):
#   "monstre"  → portraits de monstres (bestiaire)
#   "lieu"     → illustrations de salles de donjon / lieux de quête
#   "portrait" → portrait d'un PJ (ou d'un PNJ éventuellement)
USAGES_VALIDES = {"monstre", "lieu", "portrait"}

# Timeout par défaut : ComfyUI sur RTX 3060 Ti peut prendre 1-3 min pour une
# génération 4-step Lightning; on met generreusement 5 min pour le PNG final.
DEFAULT_TIMEOUT_TOTAL = 300
POLL_INTERVAL = 3.0  # secondes entre deux interrogation /history


class ComfyUIError(Exception):
    """Erreur lors de la soumission / l'attente d'un workflow ComfyUI."""


class ComfyUIBackend:
    """Cliente HTTP légère pour soumettre un workflow ComfyUI et récupérer le
    PNG final. Asynchrone (httpx), thread-safe derrière une seule instance.
    """

    def __init__(self, base_url: str = ""):
        self.base_url = (base_url or os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)
        self._workflows_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "workflows"
        )

    # ------------------------------------------------------------------ #
    def _load_workflow(self, usage: str) -> dict[str, Any]:
        """Charge le workflow JSON au format API pour un usage cible.

        Recherche prioritaire :
          1. `server/image/workflows/<usage>.json` — fichier installé.
          2. `server/image/workflows/<usage>.api.json` — alternative.

        Lève une ComfyUIError si aucun trouvé.
        """
        for name in (f"{usage}.json", f"{usage}.api.json"):
            p = os.path.join(self._workflows_dir, name)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
        raise ComfyUIError(
            f"Aucun workflow ComfyUI trouvé pour l'usage '{usage}'. "
            f"Attendu un fichier dans {self._workflows_dir}/{usage}.json "
            f"(format API, exporté depuis ComfyUI)."
        )

    # ------------------------------------------------------------------ #
    def _patch_workflow(
        self,
        graph: dict[str, Any],
        prompt_text: str,
        usage: str,
        seed: Optional[int] = None,
    ) -> tuple[dict[str, Any], int]:
        """Injecte le prompt texte et une seed dans le graphe.

        Soit le graphe expose des nodes nommés avec une convention
        `<usage>_PROMPT_NODE` / `<usage>_SEED_NODE`, soit l'heuristique
        suivante opère :
        - On cherche un node `class_type == "CLIPTextEncode"` identifié par
          clé `_PROMPT_NODE` (ie le suffixe du dict key) ou, à défaut, le
          premier CLIPTextEncode rencontré qu'on suppose être le positif
          (le 2e étant conventionnellement le négatif).
        - De même, on cherche un node portant une `seed` (KSampler /
          RandomNoise / etc.) avec la key contenant `_SEED_NODE` ou à
          défaut le premier trouvé.

        Renvoie le graphe patché et la seed effectivement utilisée.
        """
        if seed is None:
            seed = random.randint(0, 2**31 - 1)
        graph = json.loads(json.dumps(graph))  # deep copie

        # 1. Patch prompt
        prompt_node_key = None
        # Recherche par convention d'abord.
        for k in graph:
            if k.upper().endswith("_PROMPT_NODE") and k.startswith(usage.upper()):
                prompt_node_key = k
                break
        # Sinon cherche n'importe quel *_PROMPT_NODE.
        if prompt_node_key is None:
            for k, v in graph.items():
                if k.upper().endswith("_PROMPT_NODE"):
                    prompt_node_key = k
                    break
        # Dernière chance : le 1er CLIPTextEncode dans l'ordre d'insertion.
        if prompt_node_key is None:
            for k, v in graph.items():
                if (
                    isinstance(v, dict)
                    and v.get("class_type") == "CLIPTextEncode"
                    and isinstance(v.get("inputs", {}).get("text"), str)
                ):
                    prompt_node_key = k
                    break

        if prompt_node_key is not None:
            graph[prompt_node_key]["inputs"]["text"] = prompt_text

        # 2. Patch seed (recherche "carrying" par convention ou premier trouvé)
        seed_node_key = None
        for k in graph:
            if k.upper().endswith("_SEED_NODE") and k.startswith(usage.upper()):
                seed_node_key = k
                break
        if seed_node_key is None:
            for k, v in graph.items():
                if k.upper().endswith("_SEED_NODE"):
                    seed_node_key = k
                    break
        if seed_node_key is None:
            for k, v in graph.items():
                if (
                    isinstance(v, dict)
                    and isinstance(v.get("inputs"), dict)
                    and "seed" in v["inputs"]
                ):
                    seed_node_key = k
                    break
        if seed_node_key is not None:
            # Pour les nodes à noise/seed, ComfyUI accepte souvent un `seed`
            # qui doit être int. On robuste-cast.
            graph[seed_node_key]["inputs"]["seed"] = int(seed)

        return graph, seed

    # ------------------------------------------------------------------ #
    async def _submit_prompt(
        self, graph: dict[str, Any], client_id: str = "dnd35"
    ) -> str:
        """Soumet un workflow via POST /prompt et renvoie le prompt_id."""
        payload = {"prompt": graph, "client_id": client_id}
        try:
            r = await self._client.post("/prompt", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ComfyUIError(f"Échec POST /prompt : {e}") from e
        data = r.json()
        if "prompt_id" not in data:
            raise ComfyUIError(
                f"Réponse ComfyUI inattendue (pas de prompt_id) : {data}"
            )
        return data["prompt_id"]

    # ------------------------------------------------------------------ #
    async def _poll_history(self, prompt_id: str) -> dict[str, Any]:
        """Interroge /history/{prompt_id} jusqu'à ce que le résultat soit
        disponible. Renvoie l'entrée `history[prompt_id]`."""
        deadline = time.time() + DEFAULT_TIMEOUT_TOTAL
        while time.time() < deadline:
            try:
                r = await self._client.get(f"/history/{prompt_id}")
                r.raise_for_status()
            except httpx.HTTPError:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            data = r.json()
            entry = data.get(prompt_id)
            if entry:
                return entry
            await asyncio.sleep(POLL_INTERVAL)
        raise ComfyUIError(
            f"Timeout en attendant la fin de la génération (>{DEFAULT_TIMEOUT_TOTAL}s)."
        )

    # ------------------------------------------------------------------ #
    async def _download_png(
        self, history_entry: dict[str, Any], dest_path: str
    ) -> str:
        """Identifie l'image de sortie dans l'historique, la télécharge via
        /view et la sauvegarde à dest_path. Renvoie le chemin final."""
        outputs = history_entry.get("outputs") or {}
        # Le premier node avec un champ `images` (SaveImage/VSaveImage) gagne.
        for node_id, payload in outputs.items():
            images = payload.get("images") or []
            if not images:
                continue
            first = images[0]
            filename = first["filename"]
            subfolder = first.get("subfolder", "") or ""
            img_type = first.get("type", "output") or "output"
            params = {
                "filename": filename,
                "subfolder": subfolder,
                "type": img_type,
            }
            try:
                r = await self._client.get("/view", params=params)
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise ComfyUIError(
                    f"Échec téléchargement image ComfyUI ({filename}) : {e}"
                )
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return dest_path
        raise ComfyUIError(
            "Aucune image de sortie trouvée dans l'historique ComfyUI "
            f"(outputs={list(outputs.keys()) or 'vide'})."
        )

    # ------------------------------------------------------------------ #
    async def generer(
        self,
        usage: str,
        prompt_text: str,
        dest_path: str,
        seed: Optional[int] = None,
    ) -> tuple[str, int]:
        """Génère une image via ComfyUI et l'écrit dans `dest_path`.

        Renvoie `(chemin_png, seed_utilisée)`.
        """
        if usage not in USAGES_VALIDES:
            raise ComfyUIError(
                f"Usage '{usage}' inconnu. Validés : {sorted(USAGES_VALIDES)}."
            )
        graph = self._load_workflow(usage)
        graph, seed = self._patch_workflow(graph, prompt_text, usage, seed)
        prompt_id = await self._submit_prompt(graph)
        entry = await self._poll_history(prompt_id)
        await self._download_png(entry, dest_path)
        return dest_path, seed

    # ------------------------------------------------------------------ #
    async def dispo(self) -> bool:
        """Vérifie que ComfyUI est en ligne."""
        try:
            r = await self._client.get("/system_stats")
            return r.status_code == 200
        except httpx.HTTPError:
            return False
