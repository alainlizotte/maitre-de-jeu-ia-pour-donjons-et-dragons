// Types partagés alignés sur les payloads FastAPI (server/main.py).
//
// Convention : tout message WS a `type` au sommet. Les payloads « sys » portent
// un champ `event` discriminant. La narration finale arrive via `type:"dm"`.

/** État persistant d'une partie — miroir de PartyState.load() / SCHEMA_PARTIE. */
export interface PartyState {
  meta: {
    titre: string;
    cadre: string;
    regles: string;
    date_creation: string;
    date_maj: string;
  };
  phase: "opening" | "creation" | "exploration" | "combat" | "epilogue";
  tour: number;
  courant_tour_pour: string | null;
  initiative: InitiativeEntry[];
  pj: Personnage[];
  pnj: Personnage[];
  lieu: { nom: string; type: string; description: string; position_x: number; position_y: number };
  donjon: {
    id: string | null;
    salles_visitees: string[];
    portes_bloquees: string[];
    grille: unknown[];
  };
  quete: { titre: string; pitch: string; source: string };
  histoire: string[];
  derniere_narration: string;
  _erreur?: string;
}

export interface InitiativeEntry {
  nom: string;
  bonus?: number;
  jet?: number;
  init?: number;
  total?: number;
  jet_brut?: number;
  mod?: number;
  type?: "pj" | "pnj" | "monstre";
}

export interface Personnage {
  nom: string;
  joueur?: string;
  race?: string;
  classe?: string;
  niveau?: number;
  pv?: number;
  pv_max?: number;
  ca?: number;
  initiative?: number;
  conditions?: string[];
  [k: string]: unknown;
}

/** Événement de tool émis en direct par un outil (ex : "⏳ image en cours"). */
export interface ToolEvent {
  type?: string;            // ex: "image", "log", "state_patch"
  description?: string;
  image?: string;           // URL /data/... si applicable
  [k: string]: unknown;
}

/** Élément du fil de discussion rendu dans le panneau chat. */
export interface ChatMessage {
  id: string;               // id local unique (crypto.randomUUID)
  role: "user" | "assistant" | "dm" | "system";
  player?: string;
  content: string;
  image?: string;           // URL /data/... associé au message
  toolEvents?: ToolEvent[]; // events agrégés (narration dm)
  streaming?: boolean;      // en cours de stream delta
  ts: number;
}

// --------------------------------------------------------------------------- //
//  WS payloads — discriminant `type`.
// --------------------------------------------------------------------------- //
export type WsMessage =
  | { type: "sys"; event: "joined"; partie_id: string; participants: string[]; history: { role: string; content: string }[]; team_history: { player: string; text: string }[] }
  | { type: "sys"; event: "participant_joined"; player: string }
  | { type: "sys"; event: "auth_required"; detail?: string }
  | { type: "sys"; event: "auth_failed"; detail: string }
  | { type: "sys"; event: "error"; detail: string }
  | { type: "player"; player: string; text: string }
  | { type: "status"; description: string; done?: boolean }
  | { type: "delta"; text: string }
  | { type: "tool_event"; event: ToolEvent }
  | { type: "dm"; text: string; tool_events?: ToolEvent[]; state_patches?: unknown[] }
  | { type: "team_msg"; player: string; text: string }
  | { type: "audio_signal"; player: string; signal: Record<string, unknown> };

// --------------------------------------------------------------------------- //
//  REST DTOs.
// --------------------------------------------------------------------------- //
// /api/parties renvoie deux listes d'IDs (actives en mémoire + persistées disque).
export interface PartiesList {
  active: string[];
  persisted: string[];
  details: Record<string, { titre: string; phase: string; tour: number; pj: number; protegee?: boolean }>;
}

export interface HealthStatus {
  ok: boolean;
  backend: "ollama" | "llamacpp";
  backend_url: string;
  model: string;
  model_available: boolean;
  tools: string[];
  tool_mode: string;
  rag?: { enabled: boolean; collections: Record<string, number>; error?: string };
}

export interface ModelsList {
  models: string[];
  current: string;
  error?: string;
}

/** Monstre rencontré en cours de partie (galerie colonne droite). */
export interface EncounterMonster {
  url: string;   // /data/bestiaire_cache/<slug>.png|svg
  nom: string;   // nom lisible dérivé du slug
}

/** Scénario disponible pour le sélecteur de quête. */
export interface Scenario {
  id: string;
  titre: string;
  niveau: string;
  theme: string;
  pitch: string;
  source: string;
  fichier?: string | null;
}

/** /api/ressources — liens permanents affichés dans la barre de ressources. */
export interface Ressources {
  manuels: { titre: string; description: string; categorie: string; url: string }[];
  cartes: { titre: string; url: string }[];
  scenarios: { id: string; titre: string; niveau: string; url: string }[];
  donjon: string | null;
}
