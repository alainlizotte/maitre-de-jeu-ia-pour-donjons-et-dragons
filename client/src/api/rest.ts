// Client REST minimal pour /api/* (parties, health, auth, persos, tools, rag,
// modèles, fiches). fetch relatif (proxy Vite ou même origine).

import type {
  EncounterMonster,
  FichePerso,
  HealthStatus,
  ModelePerso,
  ModelsList,
  PartiesList,
  PartyState,
  Ressources,
  Universe,
} from "./types";

const API = "/api";

// --------------------------------------------------------------------------- //
//  Auth — token Bearer mémorisé (localStorage), injecté dans chaque requête.
// --------------------------------------------------------------------------- //
const TOKEN_KEY = "dnd35.token";
let surNonAuthentifie: (() => void) | null = null;

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}
export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* localStorage indisponible */
  }
}
/** Callback invoqué sur 401 (déconnexion forcée côté UI). */
export function onNonAuthentifie(cb: () => void): void {
  surNonAuthentifie = cb;
}

async function jq<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    // Session expirée → déconnexion propre (sauf sur les routes auth elles-mêmes).
    if (resp.status === 401 && !resp.url.includes("/api/auth/")) {
      setToken("");
      surNonAuthentifie?.();
    }
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* corps non JSON */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

function entetes(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra ?? {}) };
  const token = getToken();
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

function post<T>(url: string, corps?: unknown): Promise<T> {
  return fetch(url, {
    method: "POST",
    headers: entetes({ "Content-Type": "application/json" }),
    body: JSON.stringify(corps ?? {}),
  }).then(jq<T>);
}

export const api = {
  // -- Health ------------------------------------------------------------- //
  health: () => fetch(`${API}/health`).then(jq<HealthStatus>),

  // -- Auth --------------------------------------------------------------- //
  inscription: (nom: string, motDePasse: string) =>
    fetch(`${API}/auth/inscription`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nom, mot_de_passe: motDePasse }),
    }).then(jq<{ token: string; utilisateur: string }>),
  connexion: (nom: string, motDePasse: string) =>
    fetch(`${API}/auth/connexion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nom, mot_de_passe: motDePasse }),
    }).then(jq<{ token: string; utilisateur: string }>),
  moi: () => fetch(`${API}/auth/moi`, { headers: entetes() }).then(jq<{ utilisateur: string }>),

  // -- Personnages (« Mes personnages ») ----------------------------------- //
  modelePerso: () => fetch(`${API}/persos/modele`).then(jq<ModelePerso>),
  statsAleatoires: () =>
    post<{ carac: Record<string, number>; methode: string }>(`${API}/persos/stats-aleatoires`),
  orDepart: (classe: string, mode: "tirage" | "moyenne" = "tirage") =>
    post<{ or: number; formule: string }>(`${API}/persos/or-depart`, { classe, mode }),
  /** Tirage officiel âge/taille/poids selon race + classe + sexe (DRS). */
  apparenceAleatoire: (race: string, classe: string, sexe: string) =>
    post<{
      age_ans: number; taille_cm: number; poids_kg: number;
      age: string; taille: string; poids: string; race: string; formule_age: string;
    }>(`${API}/persos/apparence-aleatoire`, { race, classe, sexe }),
  listPersos: () => fetch(`${API}/persos`, { headers: entetes() }).then(jq<FichePerso[]>),
  getPerso: (slug: string) =>
    fetch(`${API}/persos/${encodeURIComponent(slug)}`, { headers: entetes() }).then(jq<FichePerso>),
  savePerso: (payload: Partial<FichePerso>) => post<{ ok: boolean; fiche: FichePerso }>(`${API}/persos`, payload),
  deletePerso: (slug: string) =>
    fetch(`${API}/persos/${encodeURIComponent(slug)}`, {
      method: "DELETE",
      headers: entetes(),
    }).then(jq<{ ok: boolean }>),

  // -- Parties ------------------------------------------------------------ //
  // /api/parties renvoie { active:[ids], persisted:[ids] } — IDs seuls.
  // Le détail (titre/phase/tour) se charge à la demande via getParty().
  listParties: () => fetch(`${API}/parties`).then(jq<PartiesList>),
  createParty: (titre: string, motDePasse?: string) =>
    fetch(`${API}/parties`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ titre, mot_de_passe: motDePasse || "" }),
    }).then(jq<{ partie_id: string }>),
  getParty: (id: string) =>
    fetch(`${API}/parties/${id}`).then(
      jq<{ partie_id: string; etat: PartyState | { _erreur: string } }>,
    ),
  deleteParty: (id: string) =>
    fetch(`${API}/parties/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: entetes(),
    }).then(jq<{ ok: boolean; partie_id: string; supprimes: string[] }>),

  // -- Modèle IA (sélection à chaud) -------------------------------------- //
  listModels: () => fetch(`${API}/models`).then(jq<ModelsList>),
  setModel: (model: string) =>
    fetch(`${API}/model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }).then(jq<{ ok: boolean; model: string }>),

  // -- Fiches personnages -------------------------------------------------- //
  getFiche: (nom: string) =>
    fetch(`${API}/fiches/${encodeURIComponent(nom)}`).then(
      jq<{ fiche: Record<string, unknown>; portrait: string | null }>,
    ),

  // -- Scénarios (sélecteur de quête) ------------------------------------ //
  listScenarios: (partieId?: string) =>
    fetch(`${API}/scenarios${partieId ? `?partie_id=${encodeURIComponent(partieId)}` : ""}`).then(
      jq<Universe[]>,
    ),
  setQuest: (partieId: string, quete: { titre: string; pitch: string; source: string }) =>
    fetch(`${API}/parties/${partieId}/quest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(quete),
    }).then(jq<{ ok: boolean; quete: { titre: string; pitch: string; source: string } }>),

  // -- Ressources (liens permanents : manuels, cartes, scénarios) ---------- //
  ressources: (partieId?: string) =>
    fetch(`${API}/ressources${partieId ? `?partie_id=${encodeURIComponent(partieId)}` : ""}`).then(
      jq<Ressources>,
    ),

  // -- Tools (introspection / docs) -------------------------------------- //
  listTools: () =>
    fetch(`${API}/tools`).then(jq<{ names: string[]; schemas: Record<string, unknown> }>),

  // -- RAG (admin) -------------------------------------------------------- //
  ragStats: () => fetch(`${API}/rag/stats`).then(jq<{ enabled: boolean; collections: Record<string, number> }>),
  ragIngest: (force = false) =>
    fetch(`${API}/rag/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    }).then(jq<{ ingested: number; skipped: number; errors: number }> ),
};

/** Déduit un nom lisible de l'URL d'une image monstre (slug → « Dragon Rouge »). */
export function monsterNameFromUrl(url: string): string {
  const m = url.match(/bestiaire_cache\/([^/.]+)\./i);
  if (!m) return "Monstre";
  return m[1]
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Construit l'entrée galerie à partir d'une URL d'image. */
export function encounterFromUrl(url: string): EncounterMonster {
  return { url, nom: monsterNameFromUrl(url) };
}
