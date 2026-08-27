// Colonne gauche — état partie condensé : lieu, phase, tour, initiative, et
// cartes des joueurs (nom du joueur au-dessus du portrait de son personnage ;
// clic sur le nom ou le portrait → fiche complète : caractéristiques,
// inventaire, sorts, etc.).

import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/rest";
import { useParty } from "../store";
import type { Personnage } from "../api/types";

/** Slug identique au `_slug` serveur (server/tools/fiches.py) pour retrouver
 *  le portrait `portraits_cache/<slug>.png` d'un personnage. */
export function slugify(text: string): string {
  const norm = text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const cleaned = norm.trim().replace(/[^A-Za-z0-9_-]+/g, "_");
  return cleaned.slice(0, 60).replace(/^_+|_+$/g, "").toLowerCase() || "perso";
}

function PhaseBadge({ phase }: { phase: string }) {
  const colors: Record<string, string> = {
    opening: "bg-sky-800 text-sky-200",
    creation: "bg-violet-800 text-violet-200",
    exploration: "bg-emerald-800 text-emerald-200",
    combat: "bg-rose-800 text-rose-200",
    epilogue: "bg-amber-800 text-amber-200",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${colors[phase] ?? "bg-stone-700"}`}>
      {phase}
    </span>
  );
}

// --------------------------------------------------------------------------- //
//  Portrait + carte joueur
// --------------------------------------------------------------------------- //
function Portrait({ nom, size = "h-40" }: { nom: string; size?: string }) {
  const [failed, setFailed] = useState(false);
  const [retries, setRetries] = useState(0);
  const slug = slugify(nom);
  const url = `/data/portraits_cache/${slug}.png`;
  const maxRetries = 5;

  // Retry loading the portrait image after a delay (in case ComfyUI is still generating)
  useEffect(() => {
    if (!failed || retries >= maxRetries) return;
    const delay = Math.min(2000 * Math.pow(2, retries - 1), 30000); // 2s, 4s, 8s, 16s, 30s
    const timer = setTimeout(() => setFailed(false), delay);
    return () => clearTimeout(timer);
  }, [failed, retries, maxRetries]);

  if (failed) {
    // Retry by forcing the <img> to re-mount with a cache-busting query param.
    // After maxRetries, show the monogram permanently.
    if (retries < maxRetries) {
      return (
        <img
          key={`${slug}-${retries}`}
          src={`${url}?t=${Date.now()}`}
          alt={nom}
          className={`${size} w-full object-contain rounded border border-stone-700 opacity-50`}
          onError={() => setRetries((r) => r + 1)}
          onLoad={() => setFailed(false)}
        />
      );
    }
    // Pas de portrait généré : monogramme stylé dérivé du nom.
    const initiales = nom
      .split(/\s+/)
      .map((w) => w.charAt(0).toUpperCase())
      .slice(0, 2)
      .join("") || "?";
    return (
      <div
        className={`${size} w-full rounded border border-stone-700 bg-gradient-to-b from-stone-800 to-stone-900 flex items-center justify-center font-serif text-3xl text-amber-500/70`}
      >
        {initiales}
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={nom}
      className={`${size} w-full object-contain rounded border border-stone-700`}
      onError={() => {
        setFailed(true);
        setRetries(1);
      }}
    />
  );
}

function PlayerCard({ pj, onOpen }: { pj: Personnage; onOpen: () => void }) {
  const joueur = (pj.joueur as string | undefined) || "(joueur inconnu)";
  const pvRatio =
    pj.pv !== undefined && pj.pv_max ? Math.max(0, Math.min(1, pj.pv / pj.pv_max)) : null;
  return (
    <button
      onClick={onOpen}
      className="w-full text-left bg-stone-800/40 hover:bg-stone-800/70 rounded p-2 transition-colors group"
      title={`Voir la fiche de ${pj.nom}`}
    >
      <div className="text-center text-xs text-amber-200/90 truncate mb-1" title={joueur}>
        👤 {joueur}
      </div>
      <Portrait nom={pj.nom} />
      <div className="mt-1 text-center">
        <div className="text-sm text-stone-100 font-medium truncate">{pj.nom}</div>
        <div className="text-xs text-stone-400">
          {[pj.race, pj.classe, pj.niveau != null ? `niv. ${pj.niveau}` : null]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
      {pvRatio !== null && (
        <div className="mt-1.5">
          <div className="h-1.5 rounded bg-stone-900 overflow-hidden">
            <div
              className={
                "h-full " +
                (pvRatio > 0.5 ? "bg-emerald-600" : pvRatio > 0.25 ? "bg-amber-600" : "bg-rose-600")
              }
              style={{ width: `${pvRatio * 100}%` }}
            />
          </div>
          <div className="text-center text-xs tabular-nums text-stone-400 mt-0.5">
            {pj.pv}/{pj.pv_max} pv{pj.ca !== undefined ? ` · CA ${pj.ca}` : ""}
          </div>
        </div>
      )}
      {(pj as { conditions?: string[] }).conditions?.length ? (
        <div className="text-xs text-amber-400 mt-1 text-center truncate">
          {(pj as { conditions: string[] }).conditions.join(", ")}
        </div>
      ) : null}
    </button>
  );
}

/** Joueur connecté sans personnage rattaché (phase de création). */
function ParticipantCard({ nom }: { nom: string }) {
  return (
    <div className="bg-stone-800/20 rounded p-2 opacity-70">
      <div className="text-center text-xs text-stone-400 truncate mb-1">👤 {nom}</div>
      <div className="h-40 w-full rounded border border-dashed border-stone-700 bg-stone-900/50 flex items-center justify-center text-stone-600 text-xs italic">
        perso à venir
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Modal fiche personnage
// --------------------------------------------------------------------------- //

/** Formate n'importe quelle valeur de fiche en texte lisible :
 *  - tableau d'objets {nom, qte} (équipement…) → « Corde ×2, Torche » ;
 *  - tableau de scalaires (dons, conditions…) → « A, B, C » ;
 *  - objet plat (carac, sauvegardes…) → « FOR 14, DEX 12 » ;
 *  - autres structures → JSON compact (jamais « [object Object] »). */
function fieldText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (item && typeof item === "object" && !Array.isArray(item)) {
          const o = item as Record<string, unknown>;
          const nom = o.nom ?? o.name ?? "";
          const qte = o.qte ?? o.quantite ?? o.quantity;
          return qte != null && Number(qte) !== 1 ? `${String(nom)} ×${qte}` : String(nom);
        }
        return String(item);
      })
      .filter(Boolean)
      .join(", ");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.every(([, v]) => typeof v !== "object")) {
      return entries.map(([k, v]) => `${k} ${v}`).join(", ");
    }
    return JSON.stringify(value);
  }
  return String(value);
}

function Field({ label, value }: { label: string; value: unknown }) {
  if (value === undefined || value === null || value === "" || value === "—") return null;
  const text = fieldText(value);
  if (!text.trim()) return null;
  return (
    <div className="mb-1.5">
      <span className="text-stone-500 text-xs">{label} : </span>
      <span className="text-stone-200 text-xs">{text}</span>
    </div>
  );
}

export function SheetModal({ nom, onClose }: { nom: string; onClose: () => void }) {
  const ficheQuery = useQuery({
    queryKey: ["fiche", nom],
    queryFn: () => api.getFiche(nom),
    retry: false,
    staleTime: 15_000,
  });
  // Repli : l'état de partie connaît déjà nom/race/classe/PV/CA même sans fiche.
  const pj = useParty((s) => s.state)?.pj?.find((p) => p.nom === nom);
  const f = (ficheQuery.data?.fiche ?? {}) as Record<string, unknown>;

  // Fermeture au clavier (Échap), cohérent avec les autres modales.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const identite = {
    nom: (f.nom as string) ?? nom,
    joueur: f.joueur ?? pj?.joueur,
    race: f.race ?? pj?.race,
    classe: f.classe ?? pj?.classe,
    niveau: f.niveau ?? pj?.niveau,
    alignement: f.alignement,
  };
  const pv = (f.pv as number) ?? pj?.pv;
  const pvMax = (f.pv_max as number) ?? pj?.pv_max;
  const ca = (f.ca as number) ?? pj?.ca;
  const chargeMax = f.charge_max;
  const portraitUrl = ficheQuery.data?.portrait;
  const sorts = f.sorts ?? f.sorts_connus;
  // Champs non rendus explicitement ci-dessous (extension libre de la fiche).
  const connus = new Set([
    "nom", "joueur", "race", "classe", "niveau", "alignement", "pv", "pv_max",
    "ca", "carac", "sauvegardes", "bab", "competences", "dons", "equipement",
    "or", "histoire", "conditions", "sorts", "sorts_connus", "charge_max",
  ]);
  const extras = Object.entries(f).filter(([k]) => !connus.has(k));

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-stone-900 border border-stone-700 rounded-lg max-w-lg w-full max-h-[85vh] overflow-auto p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            <h3 className="font-serif text-xl text-amber-200">{identite.nom}</h3>
            <p className="text-xs text-stone-400">
              {[identite.race, identite.classe, identite.niveau != null ? `niv. ${identite.niveau}` : null]
                .filter(Boolean)
                .join(" · ") || "personnage"}
            </p>
            {identite.joueur && <p className="text-xs text-stone-500">joué par {String(identite.joueur)}</p>}
          </div>
          <button onClick={onClose} className="text-stone-400 hover:text-stone-200 text-lg leading-none">
            ✕
          </button>
        </div>

        {portraitUrl && (
          <img
            src={portraitUrl}
            alt={identite.nom}
            className="w-full max-h-44 object-contain rounded border border-stone-700 mb-3"
          />
        )}

        <div className={`grid gap-2 mb-3 ${chargeMax != null ? "grid-cols-4" : "grid-cols-3"}`}>
          <div className="bg-stone-800/60 rounded p-2 text-center">
            <div className="text-lg text-rose-300 tabular-nums">{pv ?? "?"}<span className="text-xs text-stone-500">/{pvMax ?? "?"}</span></div>
            <div className="text-xs text-stone-500">PV</div>
          </div>
          <div className="bg-stone-800/60 rounded p-2 text-center">
            <div className="text-lg text-sky-300 tabular-nums">{ca ?? "?"}</div>
            <div className="text-xs text-stone-500">CA</div>
          </div>
          <div className="bg-stone-800/60 rounded p-2 text-center">
            <div className="text-lg text-amber-300 tabular-nums">
              {typeof f.bab === "number" ? (f.bab > 0 ? `+${f.bab}` : String(f.bab)) : "?"}
            </div>
            <div className="text-xs text-stone-500">BBA</div>
          </div>
          {chargeMax != null && (
            <div className="bg-stone-800/60 rounded p-2 text-center">
              <div className="text-lg text-emerald-300 tabular-nums">{String(chargeMax)}<span className="text-xs text-stone-500"> kg</span></div>
              <div className="text-xs text-stone-500">Charge max</div>
            </div>
          )}
        </div>

        {ficheQuery.isLoading && <p className="text-stone-500 text-xs italic mb-2">Chargement de la fiche…</p>}
        {ficheQuery.isError && (
          <p className="text-amber-500/80 text-xs italic mb-2">
            Pas de fiche persistante — affichage limité à l'état de partie.
          </p>
        )}

        <Field label="Alignement" value={identite.alignement} />
        <Field label="Caractéristiques" value={f.carac} />
        <Field label="Sauvegardes" value={f.sauvegardes} />
        <Field label="Compétences" value={f.competences} />
        <Field label="Dons" value={f.dons} />
        {/* Sorts : champ optionnel de la fiche (lanciers) — absent des persos martiaux. */}
        {sorts ? <Field label="Sorts" value={sorts} /> : null}
        <Field label="Équipement" value={f.equipement} />
        {f.or != null && f.or !== 0 && <Field label="Or" value={`${f.or} pc`} />}
        <Field label="Conditions" value={f.conditions} />
        {f.histoire ? (
          <div className="mt-2">
            <div className="text-xs text-stone-500 mb-0.5">Histoire :</div>
            <p className="text-xs text-stone-400 italic">{String(f.histoire)}</p>
          </div>
        ) : null}
        {extras.length > 0 && (
          <div className="mt-2 border-t border-stone-800 pt-2">
            {extras.map(([k, v]) => (
              <Field key={k} label={k} value={v} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Colonne
// --------------------------------------------------------------------------- //
export function StateSidebar() {
  const state = useParty((s) => s.state);
  const participants = useParty((s) => s.participants);
  const [ficheOuverte, setFicheOuverte] = useState<string | null>(null);

  if (!state) {
    return (
      <aside className="w-full md:w-72 h-full shrink-0 border-r-0 md:border-r border-stone-800 bg-stone-900/50 p-3 overflow-y-auto overflow-x-hidden">
        <p className="text-stone-500 text-sm italic">Chargement de l'état…</p>
      </aside>
    );
  }
  if ("_erreur" in state) {
    return (
      <aside className="w-full md:w-72 h-full shrink-0 border-r-0 md:border-r border-stone-800 bg-stone-900/50 p-3 overflow-y-auto overflow-x-hidden">
        <p className="text-rose-400 text-sm">⚠️ {state._erreur}</p>
      </aside>
    );
  }

  // Joueurs déjà rattachés à un PJ (comparaison souple casse/accents).
  const pjPlayers = new Set(
    (state.pj ?? [])
      .map((p) => String(p.joueur ?? "").trim().toLowerCase())
      .filter(Boolean),
  );
  const sansPerso = participants.filter(
    (p) => p.trim() && !pjPlayers.has(p.trim().toLowerCase()),
  );

  return (
    <aside className="w-full md:w-72 h-full shrink-0 border-r-0 md:border-r border-stone-800 bg-stone-900/50 p-3 overflow-y-auto overflow-x-hidden">
      <div className="mb-3">
        <h2 className="font-serif text-amber-200 text-sm uppercase tracking-wide">
          {state.meta?.titre || "(sans titre)"}
        </h2>
        <div className="flex items-center gap-2 mt-1 text-xs text-stone-400">
          <PhaseBadge phase={state.phase} />
          <span>tour {state.tour}</span>
        </div>
      </div>

      <div className="mb-4">
        <h3 className="text-xs uppercase text-stone-500 mb-1">Lieu</h3>
        <div className="text-stone-100 font-medium">{state.lieu?.nom}</div>
        <div className="text-xs text-stone-400">{state.lieu?.type}</div>
        {state.lieu?.description && (
          <p className="text-xs text-stone-400 mt-1 italic line-clamp-3">
            {state.lieu.description}
          </p>
        )}
      </div>

      {state.initiative && state.initiative.length > 0 && (
        <div className="mb-4">
          <h3 className="text-xs uppercase text-stone-500 mb-1">
            ⚔️ Initiative — Tour {state.tour}
          </h3>
          <ul className="space-y-0.5 text-sm">
            {state.initiative.map((it, i) => {
              // PV des combattants non-joueurs suivis (monstres, invoqués).
              const mob = (state.monstres_combat ?? []).find(
                (m) => m.nom === it.nom,
              );
              const detruit = Boolean(mob?.conditions?.includes("Détruit"));
              const pvTxt =
                mob && !mob.inconnu && mob.pv_max > 0
                  ? `${mob.pv}/${mob.pv_max} pv`
                  : mob
                    ? "pv ?"
                    : null;
              return (
                <li
                  key={i}
                  className={
                    "flex justify-between gap-1 px-2 py-1 rounded " +
                    (state.courant_tour_pour === it.nom
                      ? mob?.allie
                        ? "bg-emerald-800/50 text-emerald-100 font-medium border border-emerald-600/40"
                        : "bg-amber-800/60 text-amber-100 font-medium border border-amber-600/40"
                      : mob?.allie
                        ? "text-emerald-300/80"
                        : "text-stone-300")
                  }
                >
                  <span className="truncate">
                    {state.courant_tour_pour === it.nom && <span className="mr-1">▶</span>}
                    {mob && (mob.allie ? "🪄 " : detruit ? "☠️ " : "👹 ")}
                    <span className={detruit ? "line-through opacity-60" : ""}>{it.nom}</span>
                  </span>
                  <span className="text-stone-400 tabular-nums text-xs whitespace-nowrap">
                    {pvTxt ? `${pvTxt} · ` : ""}{it.init ?? it.total ?? "?"}
                  </span>
                </li>
              );
            })}
          </ul>
          {state.courant_tour_pour && (
            <div className="text-xs text-amber-400 mt-1 text-center italic">
              C'est au tour de <strong>{state.courant_tour_pour}</strong>
            </div>
          )}
        </div>
      )}

      <div className="mb-4">
        <h3 className="text-xs uppercase text-stone-500 mb-1">
          Joueurs ({(state.pj?.length ?? 0) + sansPerso.length})
        </h3>
        <div className="space-y-2">
          {state.pj?.map((p, i) => (
            <PlayerCard key={i} pj={p} onOpen={() => setFicheOuverte(p.nom)} />
          ))}
          {sansPerso.map((nom) => (
            <ParticipantCard key={nom} nom={nom} />
          ))}
          {(state.pj?.length ?? 0) === 0 && sansPerso.length === 0 && (
            <p className="text-xs text-stone-500 italic">Aucun joueur pour l'instant.</p>
          )}
        </div>
      </div>

      {state.pnj && state.pnj.length > 0 && (
        <div className="mt-3">
          <h3 className="text-xs uppercase text-stone-500 mb-1">
            PNJ ({state.pnj.length})
          </h3>
          <ul className="space-y-1">
            {state.pnj.map((p, i) => (
              <li key={i} className="text-sm bg-stone-800/40 rounded p-1.5">
                <div className="flex justify-between">
                  <span className="text-stone-300">{p.nom}</span>
                  {p.pv !== undefined && p.pv_max !== undefined && (
                    <span className="text-xs tabular-nums text-stone-400">
                      {p.pv}/{p.pv_max}pv
                    </span>
                  )}
                </div>
                {(p as { conditions?: string[] }).conditions?.length ? (
                  <div className="text-xs text-amber-400 mt-0.5">
                    {(p as { conditions: string[] }).conditions.join(", ")}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      {ficheOuverte && (
        <SheetModal nom={ficheOuverte} onClose={() => setFicheOuverte(null)} />
      )}
    </aside>
  );
}
