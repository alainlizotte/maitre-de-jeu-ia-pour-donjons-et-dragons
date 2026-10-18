// Colonne gauche — état partie condensé : lieu, phase, tour, initiative, et
// cartes des joueurs (nom du joueur au-dessus du portrait de son personnage ;
// clic sur le nom ou le portrait → fiche complète : caractéristiques,
// inventaire, sorts, etc.).

import { useState, useEffect, useRef, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/rest";
import { useParty } from "../store";
import type { PartyState, Personnage } from "../api/types";
import type { CombatResult } from "../api/rest";
import { busteImage } from "../utils/imageBust";
import { XpBar, ChargeBar } from "./Bars";
import { sortsEtat } from "../lib/sorts";

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
    game_over: "bg-black text-rose-300 border border-rose-900",
  };
  const libelles: Record<string, string> = {
    opening: "ouverture",
    creation: "création",
    exploration: "exploration",
    combat: "combat",
    epilogue: "épilogue",
    game_over: "💀 game over",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${colors[phase] ?? "bg-stone-700"}`}>
      {libelles[phase] ?? phase}
    </span>
  );
}

// --------------------------------------------------------------------------- //
//  Portrait + carte joueur
// --------------------------------------------------------------------------- //
function Portrait({ nom, size = "h-40" }: { nom?: string; size?: string }) {
  const [failed, setFailed] = useState(false);
  const [retries, setRetries] = useState(0);
  // 🔧 Bêta : nom optionnel — un patch d'état appliqué sur un snapshot périmé
  // crée des entrées pj PARTIELLES (sans nom) : `slugify(undefined)` plantait
  // tout le panneau (« Cannot read properties of undefined (reading
  // 'normalize') », page blanche jusqu'à un F5). Le fallback "perso" attend
  // le re-fetch REST déclenché par pj_updated.
  const slug = slugify(nom ?? "");
  const url = `/data/portraits_cache/${slug}.png`;
  // Cache-busting STABLE (même URL que la fiche et la page principale) :
  // un portrait régénéré par le serveur doit être rechargé, même pour un
  // nom de personnage réutilisé.
  const urlBuste = `${url}?t=${busteImage(url)}`;
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
          src={urlBuste}
          alt={nom}
          className={`${size} w-full object-contain rounded border border-stone-700 opacity-50`}
          onError={() => setRetries((r) => r + 1)}
          onLoad={() => setFailed(false)}
        />
      );
    }
    // Pas de portrait généré : monogramme stylé dérivé du nom.
    const initiales = (nom || "?")
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
      src={urlBuste}
      alt={nom}
      className={`${size} w-full object-contain rounded border border-stone-700`}
      onError={() => {
        setFailed(true);
        setRetries(1);
      }}
    />
  );
}

function PlayerCard({ pj, onOpen }: { pj?: Personnage; onOpen: () => void }) {
  // 🔧 Bêta : pj optionnel (patch partiel sur snapshot périmé) — rendu
  // dégradé en attendant le re-fetch REST (pj_updated), sans planter la page.
  const joueur =
    ((pj?.joueur as string | undefined) || "(joueur inconnu)");
  const nom = pj?.nom ?? "";
  const pvRatio =
    pj?.pv !== undefined && pj?.pv_max ? Math.max(0, Math.min(1, pj.pv / pj.pv_max)) : null;
  return (
    <button
      onClick={onOpen}
      className="w-full text-left bg-stone-800/40 hover:bg-stone-800/70 rounded p-2 transition-colors group"
      title={`Voir la fiche de ${nom || "(chargement…)"}`}
    >
      <div className="text-center text-xs text-amber-200/90 truncate mb-1" title={joueur}>
        👤 {joueur}
      </div>
      <Portrait nom={nom} />
      <div className="mt-1 text-center">
        <div className="text-sm text-stone-100 font-medium truncate">{nom || "…"}</div>
        <div className="text-xs text-stone-400">
          {[pj?.race, pj?.classe, pj?.niveau != null ? `niv. ${pj.niveau}` : null]
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
            {pj?.pv}/{pj?.pv_max} pv{pj?.ca !== undefined ? ` · CA ${pj.ca}` : ""}
          </div>
        </div>
      )}
      {(pj?.xp !== undefined || pj?.niveau !== undefined) && (
        <div className="mt-1.5 rounded bg-stone-900/50 border border-stone-800 px-1.5 py-1">
          <XpBar xp={Number(pj.xp ?? 0)} niveau={Number(pj.niveau ?? 1)} compact />
        </div>
      )}
      {(pj?.charge_max !== undefined || pj?.poids_transporte !== undefined) && (
        <div className="mt-1.5 rounded bg-stone-900/50 border border-stone-800 px-1.5 py-1">
          <ChargeBar
            poids={Number(pj?.poids_transporte ?? 0)}
            chargeMax={Number(pj?.charge_max ?? 0)}
            etat={typeof pj?.etat_encumbrance === "string" ? pj.etat_encumbrance : undefined}
          />
        </div>
      )}
      {(pj as { conditions?: string[] })?.conditions?.length ? (
        <div className="text-xs text-amber-400 mt-1 text-center truncate">
          {(pj as { conditions?: string[] }).conditions?.join(", ")}
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

function sexeLibelle(sexe: string): string | undefined {
  const s = (sexe || "").trim().toLowerCase();
  if (s === "m") return "Masculin";
  if (s === "f") return "Féminin";
  if (s === "autre") return "Autre";
  return s || undefined;
}

function Field({
  label,
  value,
  accentsSauves,
}: {
  label: string;
  value: unknown;
  /** Rend les clés de sauvegardes avec accents (« Reflexes » → « Réflexes »,
   *  « Volonte » → « Volonté » — clés techniques de fiche, jamais affichées
   *  brutes). */
  accentsSauves?: boolean;
}) {
  if (value === undefined || value === null || value === "" || value === "—") return null;
  let text = fieldText(value);
  if (accentsSauves && text.trim()) {
    text = text
      .replace(/Reflexes/g, "Réflexes")
      .replace(/Volonte/g, "Volonté");
  }
  if (!text.trim()) return null;
  return (
    <div className="mb-1.5">
      <span className="text-stone-500 text-xs">{label} : </span>
      <span className="text-stone-200 text-xs">{text}</span>
    </div>
  );
}

/** Entrée d'inventaire de la fiche : {nom, quantite|qte, poids_kg|poids,
 *  description, portee, partie} — armes, armures, équipement et sac.
 *  `portee === "quete"` distingue l'inventaire de QUÊTE (lié à la partie
 *  courante) de l'équipement permanent (reste au PJ d'une partie à l'autre). */
interface ItemFiche {
  nom?: string;
  name?: string;
  quantite?: number | string;
  qte?: number | string;
  poids_kg?: number | string;
  description?: string;
  portee?: string;
  partie?: string;
  charges?: number | string;
}

/** Lisible : « cle_fer » → « Cle fer » (les slugs ne sont pas des noms). */
function nomLisible(s: string): string {
  const t = String(s ?? "").replace(/[_-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function ItemListe({ items }: { items: ItemFiche[] }) {
  if (!items.length) return null;
  return (
    <ul className="space-y-1">
      {items.map((it, i) => {
        const qte = Number(it.quantite ?? it.qte ?? 1);
        const poids = it.poids_kg != null && Number(it.poids_kg) > 0
          ? `${Number(it.poids_kg)} kg`
          : null;
        return (
          <li key={`${it.nom ?? it.name}-${i}`} className="text-xs leading-snug">
            <span className="text-stone-200">
              {it.portee === "quete" && <span title="Objet de quête">📜 </span>}
              {nomLisible(it.nom ?? it.name ?? "")}
              {qte > 1 ? ` ×${qte}` : ""}
            </span>
            {poids && <span className="text-stone-500"> — {poids}</span>}
            {it.description ? (
              <span className="text-stone-500 italic"> — {it.description}</span>
            ) : null}
            {it.portee === "quete" && it.partie ? (
              <span className="text-emerald-400/70"> — partie {it.partie}</span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Bloc inventaire de la fiche : équipement porté + sac, SÉPARÉS entre
 *  inventaire de QUÊTE (objets de l'aventure en cours, `portee="quete"`) et
 *  équipement PERMANENT (reste au PJ d'une partie à l'autre). */
function InventaireBloc({ fiche }: { fiche: Record<string, unknown> }) {
  const liste = (v: unknown): ItemFiche[] =>
    Array.isArray(v)
      ? (v as ItemFiche[]).filter((it) => it && (it.nom || it.name))
      : [];
  const equipement = liste(fiche.equipement);
  const inventaire = liste(fiche.inventaire);
  const armes = liste(fiche.armes);
  const estQuete = (it: ItemFiche) => it.portee === "quete";
  // Inventaire de quête : objets tagués `portee="quete"` (dons de PNJ, objets
  // d'aventure). Affichés prioritairement, marqués de la partie d'origine.
  const quete = inventaire.filter(estQuete);
  const permanent = inventaire.filter((it) => !estQuete(it));
  // Déduplication : les tools d'inventaire miroient TOUT l'inventaire dans
  // `equipement` ET `inventaire` — sans filtre, chaque objet s'affichait
  // deux fois (« Porté / équipé » + « Sac »). On ne montre sous
  // « Porté / équipé » que les objets absents du sac.
  const cleItem = (it: ItemFiche) =>
    `${(it.nom ?? it.name ?? "").trim().toLowerCase()}|${Number(it.quantite ?? it.qte ?? 1)}`;
  const clesSac = new Set(inventaire.map(cleItem));
  const porteSeul = equipement.filter((it) => !clesSac.has(cleItem(it)));
  const aQuete = quete.length > 0;
  const aBase = armes.length > 0 || porteSeul.length > 0 || permanent.length > 0;
  if (aQuete === false && aBase === false) return null;
  return (
    <div className="mt-2 rounded bg-stone-800/40 border border-stone-800 p-2">
      <div className="text-xs text-stone-500 mb-1">Inventaire</div>
      {aQuete && (
        <div className="mb-1.5">
          <div className="text-[11px] text-emerald-300/80 mb-0.5">
            📜 Inventaire de quête
          </div>
          <ItemListe items={quete} />
        </div>
      )}
      {armes.length > 0 && (
        <div className="mb-1.5">
          <div className="text-[11px] text-amber-300/80 mb-0.5">Armes</div>
          <ItemListe items={armes} />
        </div>
      )}
      {porteSeul.length > 0 && (
        <div className="mb-1.5">
          <div className="text-[11px] text-amber-300/80 mb-0.5">Porté / équipé</div>
          <ItemListe items={porteSeul} />
        </div>
      )}
      {permanent.length > 0 && (
        <div>
          <div className="text-[11px] text-amber-300/80 mb-0.5">
            🎒 Sac — équipement permanent
          </div>
          <ItemListe items={permanent} />
        </div>
      )}
    </div>
  );
}

/** Panneau magie d'une fiche : emplacements restants (X/Y) + sorts prêts.
 *  Badge vert = prêt, barré = épuisé. Miroir des règles de server/sorts.py. */
function PanneauSorts({
  modele,
  fiche,
}: {
  modele: Parameters<typeof sortsEtat>[0];
  fiche: Parameters<typeof sortsEtat>[1];
}) {
  const etat = sortsEtat(modele, fiche);
  if (!etat) return null;
  const sortsCat = modele?.sorts ?? [];
  const niveauDe = (n: string) => sortsCat.find((s) => s.nom === n)?.niveau;
  const spontane = Boolean(
    modele?.sorts_prepare && !modele.sorts_prepare.includes(fiche.classe),
  );
  // Liste affichée : sorts connus (spontané) ou préparés du jour (préparateur).
  const badges = spontane
    ? etat.connus.map((nom) => ({ nom, nb: 1 }))
    : Object.entries(etat.prepares)
        .filter(([, nb]) => nb > 0)
        .map(([nom, nb]) => ({ nom, nb }));
  return (
    <div className="mt-2 rounded bg-purple-950/20 border border-purple-900/50 p-2">
      <div className="text-xs text-purple-300 mb-1">
        Sorts — {fiche.classe} ({spontane ? "spontané" : "préparé"})
      </div>
      <div className="flex flex-wrap gap-1 mb-1.5">
        {Object.entries(etat.slots).map(([lvl, total]) => {
          const restant = etat.restants[Number(lvl)] ?? 0;
          const epuise = restant <= 0;
          return (
            <span
              key={lvl}
              title={`Emplacements de sorts de niveau ${lvl}`}
              className={`text-[11px] px-1.5 py-0.5 rounded tabular-nums ${
                epuise
                  ? "bg-stone-800 text-stone-500"
                  : "bg-purple-900/40 text-purple-200"
              }`}
            >
              niv. {lvl} : {restant}/{total}
            </span>
          );
        })}
      </div>
      {badges.length === 0 ? (
        <p className="text-[11px] text-stone-500 italic">
          {spontane
            ? "Aucun sort connu — choisissez-les à la création."
            : "Aucun sort préparé aujourd'hui (repos + mémorisation avec le MJ)."}
        </p>
      ) : (
        <div className="flex flex-wrap gap-1">
          {badges.map(({ nom, nb }) => {
            const lvl = niveauDe(nom);
            const restant = lvl != null ? (etat.restants[lvl] ?? 0) : 1;
            const pret = restant > 0;
            return (
              <span
                key={nom}
                title={lvl != null ? `Niveau de sort ${lvl}` : nom}
                className={`text-[11px] px-1.5 py-0.5 rounded ${
                  pret
                    ? "bg-emerald-900/40 text-emerald-200"
                    : "bg-stone-800 text-stone-500 line-through"
                }`}
              >
                {nom}
                {nb > 1 ? ` x${nb}` : ""}
                {lvl != null ? ` · n${lvl}` : ""}
                {!pret && " — épuisé"}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}


export function SheetModal({ nom, onClose }: { nom: string; onClose: () => void }) {
  const stateRev = useParty((s) => s.stateRev);
  const ficheQuery = useQuery({
    queryKey: ["fiche", nom],
    queryFn: () => api.getFiche(nom),
    retry: false,
    staleTime: 15_000,
    // La fiche évolue en jeu (préparation de sorts, dégâts, conditions…) :
    // léger polling tant que la modale est ouverte.
    refetchInterval: 10_000,
  });
  // Signal serveur « une fiche a changé » (pj_updated, incrémenté dans
  // stateRev) → re-fetch immédiat : l'encadré Sorts (préparés / emplacements)
  // se met à jour dès que le MJ mémorise ou lance un sort.
  const ficheRefetch = ficheQuery.refetch;
  useEffect(() => {
    if (stateRev > 0) void ficheRefetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stateRev]);
  // Catalogues (tables de sorts) — partagés avec le formulaire de création.
  const modeleQuery = useQuery({
    queryKey: ["modelePerso"],
    queryFn: api.modelePerso,
    staleTime: Infinity,
  });
  // Repli : l'état de partie connaît déjà nom/race/classe/PV/CA même sans fiche.
  const pj = useParty((s) => s.state)?.pj?.find((p) => p.nom === nom);
  const f = (ficheQuery.data?.fiche ?? {}) as Record<string, unknown>;

  // Visionneuse plein écran : clic sur le portrait de la fiche → agrandissement.
  const [portraitAgrandi, setPortraitAgrandi] = useState(false);

  // Fermeture au clavier (Échap), cohérent avec les autres modales.
  // Si le portrait est agrandi, Échap ne ferme que la visionneuse.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !portraitAgrandi) onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, portraitAgrandi]);

  // Échap ferme la visionneuse du portrait quand elle est ouverte.
  useEffect(() => {
    if (!portraitAgrandi) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPortraitAgrandi(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [portraitAgrandi]);

  const identite = {
    nom: (f.nom as string) ?? nom,
    joueur: f.joueur ?? pj?.joueur,
    race: f.race ?? pj?.race,
    classe: f.classe ?? pj?.classe,
    niveau: f.niveau ?? pj?.niveau,
    alignement: f.alignement,
  };

  // Capacités raciales + capacités de classe acquises au niveau courant
  // (tableaux PHB 3.5 des catalogues — recalculées, rien à stocker en fiche).
  const capacites = useMemo(() => {
    const m = modeleQuery.data;
    const race = (m?.races ?? []).find((r) => r.nom === identite.race)?.capacites ?? [];
    const niv = Number(identite.niveau ?? 1);
    const classe = ((m?.classes ?? []).find((c) => c.nom === identite.classe)?.capacites ?? [])
      .filter((c) => (c.niveau ?? 1) <= niv);
    return { race, classe };
  }, [modeleQuery.data, identite.race, identite.classe, identite.niveau]);
  const pv = (f.pv as number) ?? pj?.pv;
  const pvMax = (f.pv_max as number) ?? pj?.pv_max;
  const ca = (f.ca as number) ?? pj?.ca;
  const chargeMax = f.charge_max;
  const portraitUrl = ficheQuery.data?.portrait;
  // Cache-busting STABLE : la fiche et la page principale chargent le MÊME
  // fichier avec le même cache-buster → une seule entrée cache navigateur.
  const portraitBuste = portraitUrl
    ? `${portraitUrl}${portraitUrl.includes("?") ? "&" : "?"}t=${busteImage(portraitUrl)}`
    : null;
  // Champs non rendus explicitement ci-dessous (extension libre de la fiche).
  // 🧹 Les champs techniques (debug/dev) ne sont PAS affichés : « proprietaire
  // », « avancement_confirme », « poids_transporte »… fuyaient en clair dans
  // la fiche (partie réelle) — l'UI les montre déjà via les cartes/bars dédiées.
  const connus = new Set([
    "nom", "joueur", "race", "classe", "niveau", "alignement", "pv", "pv_max",
    "ca", "carac", "sauvegardes", "bab", "competences", "dons", "equipement",
    "inventaire", "armes", "or", "histoire", "conditions", "sorts",
    "sorts_connus", "charge_max", "apparence", "capacites",
    // Champs techniques/dev jamais destinés à l'affichage brut :
    "proprietaire", "xp", "avancement_confirme", "gains_carac", "initiative",
    "poids_transporte", "etat_encumbrance", "portrait", "familier",
  ]);
  const extras = Object.entries(f).filter(([k]) => !connus.has(k));
  // Familier (Magicien/Sorcier) ou compagnon animal (Druide/Rodeur) : espèce
  // + faculté transmise + stats de base (le profil ajusté au niveau est
  // calculé en jeu par le tool appeler_familier).
  const famInfo = f.familier as
    | { type?: string; espece?: string; invoque?: boolean }
    | undefined;
  const fml = modeleQuery.data?.familiers;
  const famEspece = famInfo?.espece
    ? [...(fml?.familiers ?? []), ...(fml?.compagnons_animaux ?? []), ...(fml?.compagnons_hors_norme ?? [])].find(
        (e) => e.nom === famInfo.espece,
      )
    : undefined;
  const apparence = (f.apparence ?? pj?.apparence) as
    | Record<string, unknown>
    | undefined;

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

        {portraitBuste && (
          <img
            src={portraitBuste}
            alt={identite.nom}
            className="w-full max-h-44 object-contain rounded border border-stone-700 mb-3 cursor-zoom-in"
            title="Cliquer pour afficher en grand"
            onClick={(e) => {
              e.stopPropagation();
              setPortraitAgrandi(true);
            }}
          />
        )}

        {/* Visionneuse plein écran du portrait (au-dessus de la fiche) */}
        {portraitAgrandi && portraitBuste && (
          <div
            className="fixed inset-0 bg-black/90 flex flex-col items-center justify-center p-4 z-[60]"
            onClick={() => setPortraitAgrandi(false)}
          >
            <div
              className="relative w-full h-full max-w-4xl flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-2 shrink-0">
                <span className="text-amber-200 font-serif text-lg">{identite.nom}</span>
                <button
                  onClick={() => setPortraitAgrandi(false)}
                  className="w-9 h-9 rounded-full bg-stone-800 border border-stone-600 text-stone-300 hover:text-white text-sm"
                  title="Fermer (Échap)"
                >
                  ✕
                </button>
              </div>
              <div className="relative flex-1 min-h-0 rounded border border-stone-700 overflow-hidden bg-stone-950">
                <img
                  src={portraitBuste}
                  alt={`Portrait : ${identite.nom}`}
                  draggable={false}
                  className="absolute inset-0 w-full h-full object-contain select-none"
                />
              </div>
              <p className="text-[10px] text-stone-500 mt-1.5 text-center italic shrink-0">
                Cliquez n'importe où ou Échap pour fermer.
              </p>
            </div>
          </div>
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

        {identite.niveau != null && (
          <div className="mb-2 rounded bg-stone-800/40 border border-stone-800 p-2">
            <XpBar xp={Number(f.xp ?? 0)} niveau={Number(identite.niveau ?? 1)} />
          </div>
        )}
        {(f.poids_transporte !== undefined || f.charge_max !== undefined) && (
          <div className="mb-2 rounded bg-stone-800/40 border border-stone-800 p-2">
            <ChargeBar
              poids={Number(f.poids_transporte ?? 0)}
              chargeMax={Number(f.charge_max ?? 0)}
              etat={typeof f.etat_encumbrance === "string" ? f.etat_encumbrance : undefined}
            />
          </div>
        )}

        {ficheQuery.isLoading && <p className="text-stone-500 text-xs italic mb-2">Chargement de la fiche…</p>}
        {ficheQuery.isError && (
          <p className="text-amber-500/80 text-xs italic mb-2">
            Pas de fiche persistante — affichage limité à l'état de partie.
          </p>
        )}

        <Field label="Alignement" value={identite.alignement} />
        {apparence && (
          <div className="mt-2 rounded bg-stone-800/40 border border-stone-800 p-2">
            <div className="text-xs text-stone-500 mb-1">Apparence</div>
            <div className="grid grid-cols-2 gap-x-3">
              <Field label="Genre" value={sexeLibelle(String(apparence.sexe ?? ""))} />
              <Field label="Âge" value={apparence.age} />
              <Field label="Taille" value={apparence.taille ?? apparence.taille_physique} />
              <Field label="Poids" value={apparence.poids} />
              <Field label="Yeux" value={apparence.yeux} />
              <Field label="Cheveux" value={apparence.cheveux} />
              <Field label="Peau" value={apparence.peau} />
            </div>
            {apparence.description ? (
              <p className="text-xs text-stone-400 italic mt-1">
                {String(apparence.description)}
              </p>
            ) : null}
          </div>
        )}
        <Field label="Caractéristiques" value={f.carac} />
        <Field label="Sauvegardes" value={f.sauvegardes} accentsSauves />
        <Field label="Compétences" value={f.competences} />
        <Field label="Dons" value={f.dons} />
        {(capacites.race.length > 0 || capacites.classe.length > 0) && (
          <div className="mt-2 rounded bg-stone-800/40 border border-stone-800 p-2">
            <div className="text-xs text-stone-500 mb-1">Capacités</div>
            <ul className="space-y-1.5">
              {[
                ...capacites.race.map((c) => ({ ...c, src: `Race · ${String(identite.race ?? "")}` })),
                ...capacites.classe.map((c) => ({
                  ...c,
                  src: `${String(identite.classe ?? "")} · niv. ${c.niveau ?? 1}`,
                })),
              ].map((c) => (
                <li key={`${c.src}-${c.nom}`} className="text-xs leading-snug">
                  <span className="text-stone-200">{c.nom}</span>{" "}
                  <span className="text-stone-500">({c.src})</span>
                  {c.description ? (
                    <div className="text-[11px] text-stone-500">{c.description}</div>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        )}
        {famInfo?.espece && (
          <div className="mt-2 rounded bg-stone-800/40 border border-emerald-900/60 p-2">
            <div className="text-xs text-emerald-300/80 mb-1">
              {famInfo.type === "compagnon" ? "Compagnon animal" : "Familier"}
              {famInfo.invoque ? " · lié à son maître" : " · non encore appelé"}
            </div>
            <div className="text-xs text-stone-200">{famInfo.espece}</div>
            {famEspece && (
              <div className="text-[11px] text-stone-500">
                PV {famEspece.pv ?? "?"} · CA {famEspece.ca ?? "?"}
                {famEspece.dv ? ` · ${famEspece.dv}` : ""}
                {famEspece.faculte ? ` — ${famEspece.faculte}` : ""}
              </div>
            )}
          </div>
        )}
        <PanneauSorts
          modele={modeleQuery.data}
          fiche={{
            classe: String(identite.classe ?? ""),
            niveau: Number(identite.niveau ?? 1),
            carac: (f.carac as Record<string, number>) ?? {},
            sorts: f.sorts as
              | { connus?: string[]; prepares?: Record<string, number>; depenses?: Record<string, number> }
              | undefined,
          }}
        />
        <InventaireBloc fiche={f} />
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
// --------------------------------------------------------------------------- //
//  Actions de combat deterministes (boutons - moteur serveur, sans LLM).
//  Attaque : POST /combat/action (jet officiel + degats + rotation).
//  Fin de tour : /combat/boucle (le moteur joue les tours de monstres).
//  Attendre / Retraite : tools serveur dedies. Etat mis a jour par le poll.
// --------------------------------------------------------------------------- //
function CombatActions({ state }: { state: PartyState }) {
  const partieId = useParty((s) => s.partie_id);
  const utilisateur = useParty((s) => s.utilisateur);
  const player = useParty((s) => s.player);
  const personnage = useParty((s) => s.personnage);
  const [cible, setCible] = useState("");
  const [arme, setArme] = useState("");
  const [sort, setSort] = useState("");
  const [armes, setArmes] = useState<string[]>([]);
  const [sorts, setSorts] = useState<string[]>([]);
  const [objets, setObjets] = useState<string[]>([]);
  const [objet, setObjet] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string[]>([]);

  const actif = String(state.courant_tour_pour ?? "");
  const pjActif = (state.pj ?? []).find((p) => p.nom === actif);

  // Fiche du PJ actif : armes POSSEDEES (fiche x catalogue), sorts
  // prepares/connus et objets utilisables (potions/kits) - 48b239fa.
  const armesCatalogue = useParty((s) => s.armesCatalogue);
  useEffect(() => {
    if (armesCatalogue.length === 0) return;
    if (!actif || !pjActif) return;
    let vivant = true;
    api
      .getFiche(actif)
      .then(({ fiche }) => {
        if (!vivant) return;
        const inv = (fiche.equipement as Array<{ nom?: string }> | undefined) ?? [];
        const porte = inv.map((e) => String(e.nom ?? ""));
        // Armes POSSEDEES uniquement : nom exact dans le catalogue d'armes.
        setArmes(porte.filter((n) => armesCatalogue.includes(n)));
        const s = (fiche.sorts as {
          prepares?: Record<string, string[] | number> | string[];
          connus?: string[];
        } | undefined) ?? {};
        const prepFlat = Object.keys(s.prepares ?? {});
        setSorts(prepFlat.length > 0 ? prepFlat : (s.connus ?? []));
        // Objets utilisables : potions, fioles, kits, eau benite.
        const f_inv = (fiche.inventaire as Array<{ nom?: string }> | undefined) ?? [];
        const UTILISABLE = /potion|fiole|kit|trousse|flacon|eau b\u00e9nite|eau benite/i;
        const objets = f_inv
          .map((e) => String(e.nom ?? ""))
          .filter((n) => n && UTILISABLE.test(n));
        setObjets(objets);
      })
      .catch(() => {});
    return () => {
      vivant = false;
    };
  }, [actif, armesCatalogue.length]);
  const ennemis = (state.monstres_combat ?? []).filter(
    (m) =>
      !m.allie &&
      !(m.conditions ?? []).includes("Détruit") &&
      (m.pv ?? 1) > 0,
  );
  // Seul le joueur proprietaire du PJ actif voit les boutons.
  // Le PJ actif appartient-il au joueur connecte ? Le champ `joueur` du PJ
  // porte le pseudo WS (store `player` OU compte `utilisateur`, selon le
  // parcours) OU le nom du personnage choisi (store `personnage`).
  const mesIdentites = [utilisateur, player, personnage]
    .map((s) => String(s ?? "").trim().toLowerCase())
    .filter(Boolean);
  const aMaMain = Boolean(
    pjActif &&
      mesIdentites.includes(String(pjActif.joueur ?? "").trim().toLowerCase()),
  );

    if (state.phase !== "combat" || !aMaMain || ennemis.length === 0) {
    return null;
  }

  const run = async (fn: () => Promise<CombatResult>) => {
    setBusy(true);
    try {
      const r = await fn();
      setFeedback(r.events ?? []);
    } catch (e) {
      setFeedback([String(e)]);
    } finally {
      setBusy(false);
    }
  };

  const attaquer = () =>
    run(() =>
      api.combatAction(partieId ?? "", {
        attaquant: actif,
        cible,
        arme: arme || undefined,
        joueur: String(pjActif?.joueur ?? utilisateur),
      }),
    );
  const lancerSort = () =>
    run(() =>
      api.outil(
        partieId ?? "",
        "incanter_sort",
        {
          nom_personnage: actif,
          nom_sort: sort,
          cible: cible || actif,
        },
        String(pjActif?.joueur ?? utilisateur),
      ),
    );
  const finir = () =>
    run(() =>
      api.combatBoucle(partieId ?? "", {
        force: true,
        joueur: String(pjActif?.joueur ?? utilisateur),
      }),
    );
  const attendre = () =>
    run(() =>
      api.combatAttendre(partieId ?? "", {
        joueur: String(pjActif?.joueur ?? utilisateur),
      }),
    );
  const retraite = () =>
    run(() =>
      api.combatRetraite(partieId ?? "", {
        joueur: String(pjActif?.joueur ?? utilisateur),
      }),
    );

  return (
    <div className="mb-4 border border-rose-900/40 rounded p-2 bg-stone-900/60">
      <h3 className="text-xs uppercase text-rose-300 mb-1">
        ⚔️ Actions — {actif}
      </h3>
      <select
        value={cible}
        onChange={(e) => setCible(e.target.value)}
        className="w-full bg-stone-900 border border-stone-700 rounded px-2 py-1 text-sm mb-1"
      >
        <option value="">— Choisir une cible —</option>
        {ennemis.map((m) => (
          <option key={m.nom} value={m.nom}>
            {m.nom} ({m.pv}/{m.pv_max})
          </option>
        ))}
      </select>
      <select
        value={arme}
        onChange={(e) => setArme(e.target.value)}
        className="w-full bg-stone-900 border border-stone-700 rounded px-2 py-1 text-sm mb-1"
      >
        <option value="">— Arme (auto : équipement) —</option>
        {armes.map((a) => (
          <option key={a} value={a}>
            {a}
          </option>
        ))}
      </select>
      {sorts.length > 0 && (
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          className="w-full bg-stone-900 border border-stone-700 rounded px-2 py-1 text-sm mb-1"
        >
          <option value="">— Sort (optionnel) —</option>
          {sorts.map((sp) => (
            <option key={sp} value={sp}>
              ✨ {sp}
            </option>
          ))}
        </select>
      )}
      {objets.length > 0 && (
        <select
          value={objet}
          onChange={(e) => setObjet(e.target.value)}
          className="w-full bg-stone-900 border border-stone-700 rounded px-2 py-1 text-sm mb-1"
        >
          <option value="">— Objet à utiliser —</option>
          {objets.map((o) => (
            <option key={o} value={o}>
              🧪 {o}
            </option>
          ))}
        </select>
      )}
      <div className="grid grid-cols-2 gap-1">
        <button
          disabled={busy || !cible}
          onClick={attaquer}
          className="px-2 py-1 rounded bg-rose-700 hover:bg-rose-600 disabled:opacity-40 text-xs text-stone-100"
        >
          ⚔️ Attaquer
        </button>
        <button
          disabled={busy}
          onClick={finir}
          className="px-2 py-1 rounded bg-stone-700 hover:bg-stone-600 disabled:opacity-40 text-xs text-stone-100"
        >
          ⏭️ Fin du tour
        </button>
        <button
          disabled={busy}
          onClick={attendre}
          className="px-2 py-1 rounded bg-stone-700 hover:bg-stone-600 disabled:opacity-40 text-xs text-stone-100"
        >
          ⏳ Attendre
        </button>
        {sort && (
          <button
            disabled={busy || !sort}
            onClick={lancerSort}
            className="px-2 py-1 rounded bg-violet-700 hover:bg-violet-600 disabled:opacity-40 text-xs text-stone-100 col-span-2"
          >
            ✨ Lancer {sort}
          </button>
        )}
        {objet && (
          <button
            disabled={busy}
            onClick={() =>
              run(() =>
                api.outil(
                  partieId ?? "",
                  "fiche_perso_soigner",
                  {
                    nom: actif,
                    soin: 0,
                    source: objet,
                  },
                  String(pjActif?.joueur ?? utilisateur),
                ),
              )
            }
            className="px-2 py-1 rounded bg-emerald-800 hover:bg-emerald-700 disabled:opacity-40 text-xs text-stone-100 col-span-2"
          >
            🧪 Utiliser {objet}
          </button>
        )}
        <button
          disabled={busy}
          onClick={retraite}
          className="px-2 py-1 rounded bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-xs text-stone-100"
        >
          🏃 Retraite
        </button>
      </div>
      {feedback.length > 0 && (
        <div className="text-xs mt-1 text-stone-300 max-h-28 overflow-y-auto space-y-1">
          {feedback.map((f, i) => (
            <p key={i} className="whitespace-pre-wrap border-l-2 border-stone-700 pl-1">
              {f}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Actions rapides d'exploration (48b239fa) : repos long + marche
//  deterministes (moteur serveur, sans LLM). Visibles hors combat.
// --------------------------------------------------------------------------- //
function ActionsRapides({ state }: { state: PartyState }) {
  const partieId = useParty((s) => s.partie_id);
  const utilisateur = useParty((s) => s.utilisateur);
  const personnage = useParty((s) => s.personnage);
  const player = useParty((s) => s.player);
  const [busy, setBusy] = useState(false);
  const [ouvert, setOuvert] = useState(false);
  const [article, setArticle] = useState("");
  const [qte, setQte] = useState(1);
  const [feedback, setFeedback] = useState<string[]>([]);
  const [objets, setObjets] = useState<string[]>([]);
  const [objet, setObjet] = useState("");
  const stateRev = useParty((s) => s.stateRev);
  const bumpStateRev = useParty((s) => s.bumpStateRev);

  const joueur = String(personnage || utilisateur || player || "");

  // Objets utilisables (fiche du personnage choisi) - 48b239fa.
  useEffect(() => {
    if (!personnage) return;
    let vivant = true;
    api
      .getFiche(personnage)
      .then(({ fiche }) => {
        if (!vivant) return;
        const f_inv = (fiche.inventaire as Array<{ nom?: string }> | undefined) ?? [];
        const UTILISABLE = /potion|fiole|kit|trousse|flacon|eau b\u00e9nite|eau benite/i;
        setObjets(f_inv.map((e) => String(e.nom ?? "")).filter((n) => n && UTILISABLE.test(n)));
      })
      .catch(() => {});
    return () => {
      vivant = false;
    };
  }, [personnage, stateRev]);
  if (state.phase !== "exploration") return null;
  const lieuType = String(state.lieu?.type ?? "").toLowerCase();
  const enVille = /ville|cité|cite|bourg|village|hameau|phlan|laelith|mintarn/.test(
    lieuType + " " + String(state.lieu?.nom ?? "").toLowerCase(),
  );

  const run = async (nom: string, args: Record<string, unknown>) => {
    setBusy(true);
    try {
      const r = await api.outil(partieId ?? "", nom, args, joueur);
      setFeedback(r.events ?? []);
    } catch (e) {
      setFeedback([String(e)]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-4 border border-emerald-900/40 rounded p-2 bg-stone-900/60">
      <h3 className="text-xs uppercase text-emerald-300 mb-1">
        🎒 Actions rapides
      </h3>
      <div className="grid grid-cols-2 gap-1">
        <button
          disabled={busy}
          onClick={() => run("repos_long", { forcer: true })}
          title="Repos long de 8 h (PV + préparation de sorts)"
          className="px-2 py-1 rounded bg-sky-800 hover:bg-sky-700 disabled:opacity-40 text-xs text-stone-100"
        >
          🛏️ Repos long
        </button>
        {objets.length > 0 && (
          <select
            value={objet}
            onChange={(e) => setObjet(e.target.value)}
            className="w-full bg-stone-900 border border-stone-700 rounded px-2 py-1 text-xs mb-1 mt-1"
          >
            <option value="">— Objet à utiliser —</option>
            {objets.map((o) => (
              <option key={o} value={o}>
                🧪 {o}
              </option>
            ))}
          </select>
        )}
        {objet && (
          <button
            disabled={busy}
            onClick={() => run("fiche_perso_soigner", {
              nom: personnage || joueur,
              soin: 0,
              source: objet,
            })}
            className="w-full px-2 py-1 rounded bg-emerald-800 hover:bg-emerald-700 disabled:opacity-40 text-xs text-stone-100 mb-1"
          >
            🧪 Utiliser {objet}
          </button>
        )}
        {enVille && (
          <button
            disabled={busy}
            onClick={() => {
              setOuvert((o) => !o);
              run("marche_consulter", {});
            }}
            className="px-2 py-1 rounded bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-xs text-stone-100"
          >
            🛒 Marché
          </button>
        )}
      </div>
      {ouvert && enVille && (
        <div className="mt-1 space-y-1">
          <input
            value={article}
            onChange={(e) => setArticle(e.target.value)}
            placeholder="Article (ex : potion de soins)"
            className="w-full bg-stone-900 border border-stone-700 rounded px-2 py-1 text-xs"
          />
          <div className="flex gap-1">
            <input
              type="number"
              min={1}
              value={qte}
              onChange={(e) => setQte(Math.max(1, Number(e.target.value) || 1))}
              className="w-16 bg-stone-900 border border-stone-700 rounded px-2 py-1 text-xs"
            />
            <button
              disabled={busy || !article}
              onClick={() => run("marche_acheter", { nom: joueur, article, quantite: qte })}
              className="flex-1 px-2 py-1 rounded bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-xs"
            >
              Acheter
            </button>
            <button
              disabled={busy || !article}
              onClick={() => run("marche_vendre", { nom: joueur, article, quantite: qte })}
              className="flex-1 px-2 py-1 rounded bg-stone-700 hover:bg-stone-600 disabled:opacity-40 text-xs"
            >
              Vendre
            </button>
          </div>
        </div>
      )}
      {feedback.length > 0 && (
        <div className="text-xs mt-1 text-stone-300 max-h-32 overflow-y-auto space-y-1">
          {feedback.map((f, i) => (
            <p key={i} className="whitespace-pre-wrap border-l-2 border-stone-700 pl-1">
              {f}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

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
          {/* « tour » n'a de sens qu'en combat (round) : hors combat il
              affichait « tour 0 » en permanence, sémantique trompeuse. */}
          {state.phase === "combat" && <span>round {state.tour}</span>}
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
              // PV des PJ directement depuis l'état (fiche + barres du front,
              // synchronisés par WS) — les joueurs sont suivis comme les
              // monstres dans la liste d'initiative.
              const pj = (state.pj ?? []).find((p) => p.nom === it.nom);
              const pvJoueur =
                pj && pj.pv !== undefined
                  ? `${pj.pv}/${pj.pv_max ?? "?"} pv`
                  : null;
              const pvFinal = pvTxt ?? pvJoueur;
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
                    {pvFinal ? `${pvFinal} · ` : ""}init. {it.init ?? it.total ?? "?"}
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
      <CombatActions state={state} />
      <ActionsRapides state={state} />

      <div className="mb-4">
        <h3 className="text-xs uppercase text-stone-500 mb-1">
          Joueurs ({(state.pj?.length ?? 0) + sansPerso.length})
        </h3>
        <div className="space-y-2">
          {state.pj?.map((p, i) => (
            <PlayerCard
              key={i}
              pj={p ?? undefined}
              onOpen={() => { if (p?.nom) setFicheOuverte(p.nom); }}
            />
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
