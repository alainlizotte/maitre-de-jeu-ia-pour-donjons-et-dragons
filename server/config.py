"""Charge et valide la configuration YAML de l'application D&D 3.5."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class LLMConfig:
    backend: str = "ollama"          # "ollama" | "llamacpp"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "gemma4:12b"
    temperature: float = 0.75
    top_p: float = 0.9
    max_context_tokens: int = 8192
    tool_mode: str = "prompt"        # "native" | "prompt" | "auto"
    detect_simulation: bool = True
    max_tool_iterations: int = 10
    think: bool = False              # désactive le thinking/réflexion (Gemma 4, Qwen3…)
    # Déchargement du modèle après le tour du MJ :
    # - True  : comportement historique — la VRAM est libérée dès la fin du
    #           dernier tour actif (partage du GPU avec ComfyUI).
    # - False : le modèle reste chargé et n'est déchargé qu'après
    #           `unload_delay_minutes` minutes d'inactivité — utile avec plus
    #           de RAM/VRAM : les tours consécutifs évitent de recharger le
    #           modèle (gain de plusieurs secondes par tour).
    unload_after_turn: bool = True
    unload_delay_minutes: float = 5.0
    # Options natives transmises à Ollama via le champ `options` du payload
    # OpenAI-compatible (ex: num_ctx, top_k, seed, …). Calibré par l'utilisateur
    # pour Gemma 4 12B : {num_ctx: 60000, top_k: 64}.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:8000"]
    )


@dataclass
class PathsConfig:
    data_dir: str = "./server/data"
    prompts_dir: str = "./server/prompts"
    sections_dir: str = "./server/prompts/sections"


@dataclass
class GameConfig:
    default_title: str = "Partie D&D 3.5 — Côte des Épées"
    default_frame: str = "Côte des Épées (Faerûn)"
    max_history_events: int = 50
    max_recap_chars: int = 6000
    stream_to_clients: bool = True


@dataclass
class RagConfig:
    enabled: bool = False
    source_dir: str = "../Dongeon dragon/projet_DnD35/knowledge_import"
    persist_dir: str = "./server/data/chroma"
    embedding_model: str = "nomic-embed-text-v1.5"
    # Serveur d'embeddings dédié (llama.cpp `--embedding`). Vide → retombe sur
    # `llm.base_url` (comportement historique Ollama, embeddings via le LLM).
    embedding_base_url: str = ""
    chunk_size: int = 1500
    chunk_overlap: int = 200
    top_k: int = 5
    query_max_tokens: int = 1500
    # `None` = interroge toutes les KB ; liste pour restreindre.
    kb_filter: Optional[list[str]] = None


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    game: GameConfig = field(default_factory=GameConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    project_root: Path = Path(__file__).resolve().parent.parent

    def abs(self, path: str) -> Path:
        """Résout `path` (relatif ou absolu) depuis la racine projet."""
        p = Path(path)
        return p if p.is_absolute() else (self.project_root / p).resolve()


def _coerce(dataclass_cls, data: dict[str, Any]):
    """N'hydrate que les champs connus du dataclass pour éviter les crashes."""
    if not isinstance(data, dict):
        return dataclass_cls()
    fields = {f for f in dataclass_cls.__dataclass_fields__}
    return dataclass_cls(**{k: v for k, v in data.items() if k in fields})


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Charge config.yaml (et retombe sur les défauts si absent)."""
    project_root = Path(__file__).resolve().parent.parent
    if path is None:
        path = project_root / "config" / "config.yaml"
    path = Path(path)

    raw: dict[str, Any] = {}
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    cfg = AppConfig(
        llm=_coerce(LLMConfig, raw.get("llm", {})),
        server=_coerce(ServerConfig, raw.get("server", {})),
        paths=_coerce(PathsConfig, raw.get("paths", {})),
        game=_coerce(GameConfig, raw.get("game", {})),
        rag=_coerce(RagConfig, raw.get("rag", {})),
        raw=raw,
        project_root=project_root,
    )

    # S'assure que les dossiers critiques existent
    for p in (cfg.paths.data_dir, cfg.paths.prompts_dir, cfg.paths.sections_dir, cfg.rag.persist_dir):
        d = cfg.abs(p)
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return cfg


# Singleton chargé paresseusement
_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: AppConfig) -> None:
    global _config
    _config = cfg
