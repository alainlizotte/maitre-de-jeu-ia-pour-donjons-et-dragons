// Client REST minimal pour /api/* (parties, health, tools, rag, modèles, fiches).
// Pas d'auth — app locale mono-utilisateur. fetch relatif (proxy Vite ou même origine).

import type { EncounterMonster, HealthStatus, ModelsList, PartiesList, PartyState, Ressources } from "./types";

const API = "/api";

async function jq<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    // Le serveur renvoie {"detail": "..."} sur les erreurs HTTPException.
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

export const api = {
  // -- Health ------------------------------------------------------------- //
  health: () => fetch(`${API}/health`).then(jq<HealthStatus>),

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
