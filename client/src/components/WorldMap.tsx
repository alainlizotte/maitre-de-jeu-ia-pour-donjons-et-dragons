// Carte du monde — image adaptée à l'univers de la quête en cours.
// Affichage statique : la carte remplit 100 % de la fenêtre de l'onglet ;
// un clic l'ouvre en plein écran (fermeture par clic ou Échap).
// La position du groupe n'est plus affichée (aucun indicateur ni étiquette).

import { useEffect, useState } from "react";
import { useParty } from "../store";

// ── Lookup univers → carte par défaut ──────────────────────────────────── //
// Clé = ID d'univers dans scenarios_catalogue.json ; valeur = URL de la
// carte principale + dimensions natives (pour le ratio d'affichage).
interface MapInfo { url: string; w: number; h: number; label: string; atlas?: string }

const FALLBACK_MAP: MapInfo = {
  url: "/data/scenarios/Les Royaumes Oubliés/Cartes/faerun_nord.png",
  w: 1137, h: 928, label: "Faerûn — Nord",
  atlas: "https://www.aidedd.org/atlas/fr/faerun",
};

const UNIVERSE_MAPS: Record<string, MapInfo> = {
  royaumes_oublies: FALLBACK_MAP,
  laelith: {
    url: "/data/scenarios/Laelith/Cartes/laelith.jpg",
    w: 1200, h: 900, label: "Laelith",
    atlas: "https://www.aidedd.org/atlas/fr/laelith",
  },
  terres_eternel: {
    url: "/data/scenarios/Les Terres de l'Éternel/Cartes/atlas.jpg",
    w: 1200, h: 900, label: "Les Terres de l'Éternel",
  },
  divers: FALLBACK_MAP,
};

// ── Surcharge par scénario ─────────────────────────────────────────────── //
// Certains scénarios ont leur propre carte du monde, plus pertinente que
// la carte d'univers. Clé = ID du scénario dans scenarios_catalogue.json.
const SCENARIO_MAPS: Record<string, MapInfo> = {
  terres_dragon_hurlemont: {
    url: "/data/scenarios/Les Terres de l'Éternel/Cartes/atlas.jpg",
    w: 1200, h: 900, label: "Les Terres de l'Éternel",
  },
  ro_to_find_a_gate: {
    url: "/data/scenarios/Les Royaumes Oubliés/To Find a Gate/Cartes/Spine of the World.jpg",
    w: 800, h: 600, label: "Spine of the World",
  },
  divers_army_of_the_damned: {
    url: "/data/scenarios/Divers/Army of the Damned/Maps/Innistrad Map.png",
    w: 1200, h: 900, label: "Innistrad",
  },
};

/** Extrait l'ID d'univers et l'ID scénario à partir de la source de la quête.
 *  Formats supportés :
 *   - Neuf : `[univers_scenario_id] /data/scenarios/...`
 *   - Ancien : `/data/scenarios/Laelith/...` (détection par chemin) */
function extractIds(questSource?: string): { universe: string; scenario: string } {
  const src = questSource ?? "";
  // Format neuf : [prefix_scenario] ...
  const m = src.match(/^\[([a-z_]+?)_/);
  if (m?.[1]) {
    // Extraire l'ID complet du scénario (tout ce qui est entre [ et ])
    const fullId = src.match(/^\[([^\]]+)\]/)?.[1] ?? "";
    return { universe: m[1], scenario: fullId };
  }
  // Format ancien : détecter l'univers dans le chemin /data/scenarios/<Univers>/...
  const lower = src.toLowerCase();
  if (lower.includes("/scenarios/laelith/")) return { universe: "laelith", scenario: "" };
  if (lower.includes("/scenarios/les royaumes") || lower.includes("/scenarios/les_royaumes")) return { universe: "royaumes_oublies", scenario: "" };
  if (lower.includes("/scenarios/les terres") || lower.includes("/scenarios/les_terres")) return { universe: "terres_eternel", scenario: "" };
  if (lower.includes("/scenarios/divers/")) return { universe: "divers", scenario: "" };
  return { universe: "", scenario: "" };
}

// ── Atlas interactif ───────────────────────────────────────────────────── //
const ATLAS_VILLES_FAERUN = new Set(
  [
    "Mirabar", "Luskan", "Neverwinter", "Waterdeep", "Daggerford", "Triboar",
    "Phandalin", "Everlund", "Silverymoon", "Mithral Hall", "Evereska",
    "Secomber", "Scornubel", "Elturel", "Baldur's Gate", "Athkatla", "Suzail",
  ].map(sansAccentsMin)
);

function sansAccentsMin(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

export function WorldMap() {
  const state = useParty((s) => s.state);
  const lieu = state?.lieu;
  // Déterminer la carte : scénario > univers > fallback
  const { universe: universeId, scenario: scenarioId } = extractIds(state?.quete?.source);
  const mapInfo = (scenarioId ? SCENARIO_MAPS[scenarioId] : undefined)
    ?? UNIVERSE_MAPS[universeId]
    ?? FALLBACK_MAP;
  const MAP_URL = mapInfo.url;
  const ATLAS_BASE = mapInfo.atlas;

  // Atlas link (si ville connue sur carte Faerûn)
  const nomAtlas = (() => {
    if (!ATLAS_BASE) return "";
    const brut = (lieu?.nom ?? "").split(",").pop()?.trim() ?? "";
    return ATLAS_VILLES_FAERUN.has(sansAccentsMin(brut)) ? brut : "";
  })();
  const urlAtlas = nomAtlas ? `${ATLAS_BASE}@${encodeURIComponent(nomAtlas)}` : null;

  const [erreur, setErreur] = useState(false);
  const [pleinEcran, setPleinEcran] = useState(false);

  // Fermeture du plein écran au clavier (Échap).
  useEffect(() => {
    if (!pleinEcran) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPleinEcran(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [pleinEcran]);

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="mb-2 text-sm text-amber-200 font-serif text-center shrink-0">
        {mapInfo.label}
      </div>
      {/* Carte statique : 100 % de la fenêtre, clic → plein écran */}
      <div
        className="relative flex-1 min-h-40 rounded border border-stone-700 overflow-hidden bg-stone-950 cursor-zoom-in"
        title="Cliquer pour afficher en plein écran"
        onClick={() => setPleinEcran(true)}
      >
        <img
          src={MAP_URL}
          alt={`Carte : ${mapInfo.label}`}
          draggable={false}
          onError={() => setErreur(true)}
          className="absolute inset-0 w-full h-full object-cover select-none pointer-events-none"
        />
        {erreur && (
          <div className="absolute inset-0 flex items-center justify-center text-center text-stone-500 text-xs italic px-6">
            Carte introuvable sur le serveur —
            redémarrez le serveur pour la copier dans data/scenarios/.
          </div>
        )}
      </div>
      {/* Lien atlas externe (ressource, pas un contrôle de zoom) */}
      {urlAtlas && (
        <div className="flex justify-center mt-2 shrink-0">
          <a
            href={urlAtlas}
            target="_blank"
            rel="noreferrer"
            className="px-2 h-7 flex items-center rounded bg-stone-700 hover:bg-stone-600 text-sky-300 text-[10px]"
            title={`Voir ${nomAtlas} sur l'atlas interactif AideDD (internet)`}
          >
            🌐 Atlas
          </a>
        </div>
      )}
      <p className="text-[10px] text-stone-500 mt-1.5 text-center italic shrink-0">
        Cliquez sur la carte pour l'agrandir.
      </p>

      {/* Plein écran : clic sur la carte → visionneuse occupant tout l'écran */}
      {pleinEcran && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex flex-col items-center justify-center p-4"
          onClick={() => setPleinEcran(false)}
        >
          <div
            className="relative w-full h-full max-w-7xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2 shrink-0">
              <span className="text-amber-200 font-serif text-lg">{mapInfo.label}</span>
              <button
                onClick={() => setPleinEcran(false)}
                className="w-9 h-9 rounded-full bg-stone-800 border border-stone-600 text-stone-300 hover:text-white text-sm"
                title="Fermer (Échap)"
              >
                ✕
              </button>
            </div>
            <div className="relative flex-1 min-h-0 rounded border border-stone-700 overflow-hidden bg-stone-950">
              <img
                src={MAP_URL}
                alt={`Carte plein écran : ${mapInfo.label}`}
                draggable={false}
                onError={() => setErreur(true)}
                className="absolute inset-0 w-full h-full object-contain select-none"
              />
            </div>
            <p className="text-[10px] text-stone-500 mt-1.5 text-center italic shrink-0">
              Cliquez n'importe où ou Échap pour fermer.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
