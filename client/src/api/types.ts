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
    /** Coordonnées [x, y] de la salle où se trouve le groupe. */
    courant?: number[];
  };
  donjons_exploreres?: Record<string, {
    id: string | null;
    salles_visitees: string[];
    portes_bloquees: string[];
    grille: unknown[];
    courant?: number[];
  }>;
  quete: { titre: string; pitch: string; source: string };
  histoire: string[];
  derniere_narration: string;
  /** Combattants non-JJ suivis mécaniquement pendant le combat. */
  monstres_combat?: MonstreCombat[];
  /** Journal des illustrations de monstres croisés ({nom, url}). */
  rencontres_images?: { nom: string; url: string }[];
  _erreur?: string;
}

export interface MonstreCombat {
  nom: string;
  pv: number;
  pv_max: number;
  ca?: number | null;
  fp?: string;
  conditions?: string[];
  /** true = invoqué/allié combattant pour les joueurs. */
  allie?: boolean;
  inconnu?: boolean;
  /** Illustration persistée (survit aux rechargements, jusqu'à la mort). */
  image_url?: string;
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
  | { type: "sys"; event: "participant_joined"; player: string; personnage?: string | null }
  | { type: "sys"; event: "auth_required"; detail?: string }
  | { type: "sys"; event: "auth_failed"; detail: string }
  | { type: "sys"; event: "join_refused"; detail: string }
  | { type: "sys"; event: "turn_blocked"; detail: string }
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

/** /api/settings/images — génération d'images (globale + scènes seules). */
export interface ImageSettings {
  enabled: boolean;
  scenes_enabled: boolean;
  /** Verrou dur config.yaml (image.scenes_enabled) — à false, l'onglet
   *  « Scènes » et son bouton sont retirés de la galerie. */
  scenes_config_enabled: boolean;
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
  pitch: string;
  niveau?: string;
  joueurs?: string;
  pdf?: string | null;
  cartes?: { nom: string; fichier: string }[];
  artwork?: {
    lieux?: { nom: string; fichier: string }[];
    monstres?: { nom: string; fichier: string }[];
    pnj?: { nom: string; fichier: string }[];
  };
  objets?: { nom: string; fichier: string }[];
  enigmes?: { nom: string; fichier: string }[];
  annexes?: { nom: string; fichier: string }[];
}

/** Univers contenant des scénarios. */
export interface Universe {
  id: string;
  nom: string;
  description: string;
  cartes?: { nom: string; fichier: string }[];
  scenarios: Scenario[];
}

/** /api/ressources — liens permanents affichés dans la barre de ressources. */
export interface Ressources {
  manuels: { titre: string; description: string; categorie: string; url: string }[];
  cartes: { titre: string; url: string; libelle?: string }[];
  scenarios: { id: string; titre: string; niveau: string; url: string }[];
  donjon: string | null;
}

// --------------------------------------------------------------------------- //
//  Authentification (comptes locaux) — /api/auth/*
// --------------------------------------------------------------------------- //
export interface AuthResponse {
  token: string;
  utilisateur: string;
}

// --------------------------------------------------------------------------- //
//  Personnages joueurs — « Mes personnages » (/api/persos/*)
// --------------------------------------------------------------------------- //
export type CaracCle = "FOR" | "DEX" | "CON" | "INT" | "SAG" | "CHA";
export type CaracMap = Record<CaracCle, number>;

export interface Apparence {
  sexe?: string;
  age?: string;
  taille_physique?: string;
  poids?: string;
  yeux?: string;
  cheveux?: string;
  peau?: string;
  description?: string;
}

/** Fiche personnage complète — miroir de server/persos.py. */
export interface FichePerso {
  nom: string;
  joueur?: string;
  proprietaire?: string;
  race: string;
  classe: string;
  niveau: number;
  /** Points d'expérience courants (montée de niveau officielle 3.5). */
  xp?: number;
  carac: CaracMap;
  pv: number;
  pv_max: number;
  ca: number;
  sauvegardes: { Vigueur: number; Reflexes: number; Volonte: number };
  bab: number;
  initiative?: number;
  /** Charge maximale transportable en kg (Force × taille). */
  charge_max?: number;
  /** Poids transporté actuel en kg (depuis le catalogue PHB 3.5). */
  poids_transporte?: number;
  /** Catégorie d'encombrement : Legere / Moyenne / Lourde / Depassee. */
  etat_encumbrance?: "Legere" | "Moyenne" | "Lourde" | "Depassee" | string;
  competences?: Record<string, number>;
  dons?: string[];
  equipement?: { nom: string; qte: number; poids?: number }[];
  or?: number;
  alignement?: string;
  dieu?: string;
  histoire?: string;
  conditions?: string[];
  apparence?: Apparence;
  portrait?: string | null;
}

export interface RaceModele {
  nom: string;
  mods: Partial<Record<string, number>>;
  taille: string;
  vitesse: number;
}

export interface ClasseModele {
  nom: string;
  de_vie: number;
  bab: "bon" | "moyen" | "mauvais";
  sauves_bonnes: ("Vigueur" | "Reflexes" | "Volonte")[];
}

export interface DieuModele {
  nom: string;
  titre: string;
  alignement: string;
  /** Races servies ([] = ouvert à toutes). */
  races: string[];
  /** Classes servies ([] = ouvertes à toutes). */
  classes: string[];
  /** true = serviteurs maléfiques uniquement. */
  mal: boolean;
}

export interface ArmeModele {
  nom: string;
  groupe: "simple" | "martiale";
  distance: boolean;
  degats: string;
  cout: number;
  /** Poids d'une unité en kg (PHB 3.5). */
  poids?: number;
}

export interface ArmureModele {
  nom: string;
  categorie: "Legere" | "Moyenne" | "Lourde" | "Bouclier";
  ca: number;
  dex_max: number | null;
  malus: number;
  cout: number;
  /** Poids d'une unité en kg (PHB 3.5, taille Moyenne). */
  poids?: number;
}

export interface ObjetModele {
  nom: string;
  cout: number;
  /** Poids d'une unité en kg (PHB 3.5). */
  poids?: number;
}

export interface DonModele {
  nom: string;
  condition: string;
  prereq: Partial<Record<"for" | "dex" | "int" | "sag" | "bab", number>>;
}

export interface CompetenceModele {
  nom: string;
  cara: string;
}

export interface ProficiencesClasse {
  armures: ("Legere" | "Moyenne" | "Lourde")[];
  boucliers: boolean;
  groupes: ("simple" | "martiale")[];
  specifiques: string[];
}

export interface OrDepartFormule {
  des: string;
  mult: number;
}

export interface ModelePerso {
  races: RaceModele[];
  classes: ClasseModele[];
  alignements: string[];
  dieux: DieuModele[];
  proficiences: Record<string, ProficiencesClasse>;
  armes: ArmeModele[];
  armures: ArmureModele[];
  equipement_aventurier: ObjetModele[];
  dons: DonModele[];
  competences: CompetenceModele[];
  competences_classe: Record<string, string[]>;
  points_competence: Record<string, number>;
  or_depart: Record<string, OrDepartFormule>;
}
