"""Sessions de partie D&D 3.5 multijoueur.

Gère :
- l'historique conversationnel (messages OpenAI) par `partie_id`,
- le registre des connexions WebSocket actives par partie (pour le broadcast
  des narrations + events aux joueurs connectés),
- l'identification du joueur auteur d'un message (préfixe `**[Nom]** :`),
- la persistance/rejeu de l'historique : sauvegarde atomique sur disque à
  chaque mutation pour survivre aux redémarrages serveur.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..llm.client import Message


# --------------------------------------------------------------------------- #
#  Session d'une partie
# --------------------------------------------------------------------------- #
@dataclass
class PartySession:
    partie_id: str
    history: list[Message] = field(default_factory=list)
    connections: set[Any] = field(default_factory=set)
    authenticated: set[Any] = field(default_factory=set)
    participants: list[str] = field(default_factory=list)
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    data_dir: Optional[str] = None
    max_history_events: int = 50
    # Chat d'équipe (joueurs ↔ joueurs, sans le MJ).
    team_history: list[dict[str, str]] = field(default_factory=list)
    # Indique que le MJ est en cours de traitement (pensée/génération).
    # Les messages "say" des joueurs sont rejetés tant que thinking est True.
    thinking: bool = False

    def add_participant(self, name: str) -> None:
        if name and name not in self.participants:
            self.participants.append(name)

    # ------------------------------------------------------------------ #
    #  Historique conversationnel
    # ------------------------------------------------------------------ #
    def remember_player_message(self, player: str, text: str) -> Message:
        """Préfixe du joueur + création du message user (ex. de la doc)."""
        # Supprime un éventuel double préfixe : `**[Alain]** : déjà-préfixé`.
        stripped = text.strip()
        if not stripped.startswith("**["):
            wrapped = f"**[{player}]** : {stripped}"
        else:
            wrapped = stripped
        msg = Message(role="user", content=wrapped)
        self.history.append(msg)
        self._truncate_and_persist()
        return msg

    def remember_assistant(self, content: str, tool_calls: Optional[list[dict]] = None) -> None:
        self.history.append(
            Message(role="assistant", content=content, tool_calls=tool_calls)
        )
        self._truncate_and_persist()

    def remember_tool(self, name: str, tool_call_id: str, content: str) -> None:
        self.history.append(
            Message(
                role="tool",
                name=name,
                tool_call_id=tool_call_id,
                content=content,
            )
        )
        self._truncate_and_persist()

    def _truncate_and_persist(self) -> None:
        """Tronque l'historique et persiste (appelé après chaque append)."""
        if len(self.history) > self.max_history_events:
            self.history = self.history[-self.max_history_events :]
        self._persist_history()

    # ------------------------------------------------------------------ #
    #  Persistance / hydratation
    # ------------------------------------------------------------------ #
    @property
    def _chat_path(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        return Path(self.data_dir) / f"chat_{self.partie_id}.json"

    def _persist_history(self) -> None:
        """Écrit l'historique en JSON atomiquement (best-effort, non bloquant).

        On sérialise une forme simplifiée (role/content + tool_calls réduit à
        leur `signature` stable) — pas la structure complète OpenAI (qui
        contient des champs non round-trippables). Le rejeu au join s'appuie
        sur role+content uniquement.
        """
        path = self._chat_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [self._msg_to_dict(m) for m in self.history]
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=".chat_", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError:
            # La persistance du chat est best-effort : on ne fait jamais
            # planter un tour de narration à cause d'un échec d'écriture.
            pass

    def hydrate_history(self) -> None:
        """Charge l'historique persistant au démarrage (au plus max_events)."""
        path = self._chat_path
        if path is None or not path.is_file():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        msgs: list[Message] = []
        for entry in data:
            try:
                msgs.append(self._msg_from_dict(entry))
            except (KeyError, TypeError):
                continue
        if msgs:
            self.history = msgs[-self.max_history_events :]

    @staticmethod
    def _msg_to_dict(m: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d

    @staticmethod
    def _msg_from_dict(d: dict[str, Any]) -> Message:
        return Message(
            role=d.get("role", "user"),
            content=d.get("content", ""),
            tool_calls=d.get("tool_calls"),
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
        )

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """Envoie un payload JSON à toutes les connexions actives de la partie."""
        dead: list[Any] = []
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.discard(ws)

    async def broadcast_team(self, payload: dict[str, Any]) -> None:
        """Envoie un payload aux connexions joueurs (exclut le MJ/DM si identifié).

        Convention : le MJ est le premier participant ou celui qui a rejoint
        en premier. On broadcast à tous les participants, le MJ verra les
        messages mais ne les recevra pas en priorité (broadcast identique).
        """
        await self.broadcast(payload)

    # ------------------------------------------------------------------ #
    #  Chat d'équipe — persistance
    # ------------------------------------------------------------------ #
    @property
    def _team_chat_path(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        return Path(self.data_dir) / f"team_chat_{self.partie_id}.json"

    def remember_team_message(self, player: str, text: str) -> None:
        self.team_history.append({"player": player, "text": text})
        self._persist_team_history()

    def _persist_team_history(self) -> None:
        path = self._team_chat_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=".team_chat_", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.team_history[-200:], f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass

    def hydrate_team_history(self) -> None:
        path = self._team_chat_path
        if path is None or not path.is_file():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.team_history = json.load(f)[-200:]
        except (json.JSONDecodeError, OSError):
            pass


# --------------------------------------------------------------------------- #
#  Registry global (singleton) — une session par partie_id
# --------------------------------------------------------------------------- #
class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, PartySession] = {}
        self._data_dir: Optional[str] = None
        self._max_history_events: int = 50

    def configure(self, data_dir: str, max_history_events: int = 50) -> None:
        """Initialise le registre avec le `data_dir` de l'app — à appeler au
        démarrage (lifespan) avant tout `get()`."""
        self._data_dir = data_dir
        self._max_history_events = max_history_events

    def get(self, partie_id: str) -> PartySession:
        if partie_id not in self._sessions:
            sess = PartySession(
                partie_id=partie_id,
                data_dir=self._data_dir,
                max_history_events=self._max_history_events,
            )
            sess.hydrate_history()
            sess.hydrate_team_history()
            self._sessions[partie_id] = sess
        return self._sessions[partie_id]

    def all_ids(self) -> list[str]:
        return list(self._sessions.keys())

    def pop(self, partie_id: str) -> Optional[PartySession]:
        """Retire et renvoie la session en mémoire (suppression de partie).

        Renvoie None si la partie n'avait pas de session active."""
        return self._sessions.pop(partie_id, None)


# Singleton
registry = SessionRegistry()
