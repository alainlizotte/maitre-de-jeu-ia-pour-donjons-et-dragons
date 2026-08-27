// Carte du monde — image adaptée à l'univers de la quête en cours.
// Le marqueur doré du groupe est positionné en pourcentage de l'image :
// lieu.position_x (0 = ouest → 100 = est) et lieu.position_y (0 = nord →
// 100 = sud), patchés en direct par le MJ via carte_joueurs_placer_ville
// / carte_joueurs_position. Zoom à boutons + glisser pour naviguer,
// recentrage auto sur le groupe.

import { useEffect, useRef, useState } from "react";
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
  const mx = lieu?.position_x;
  const my = lieu?.position_y;
  const aPosition = mx != null && my != null;

  // Déterminer la carte : scénario > univers > fallback
  const { universe: universeId, scenario: scenarioId } = extractIds(state?.quete?.source);
  const mapInfo = (scenarioId ? SCENARIO_MAPS[scenarioId] : undefined)
    ?? UNIVERSE_MAPS[universeId]
    ?? FALLBACK_MAP;
  const MAP_URL = mapInfo.url;
  const MAP_W = mapInfo.w;
  const MAP_H = mapInfo.h;
  const ATLAS_BASE = mapInfo.atlas;

  // Atlas link (si ville connue sur carte Faerûn)
  const nomAtlas = (() => {
    if (!ATLAS_BASE) return "";
    const brut = (lieu?.nom ?? "").split(",").pop()?.trim() ?? "";
    return ATLAS_VILLES_FAERUN.has(sansAccentsMin(brut)) ? brut : "";
  })();
  const urlAtlas = nomAtlas ? `${ATLAS_BASE}@${encodeURIComponent(nomAtlas)}` : null;

  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [erreur, setErreur] = useState(false);
  const [dragging, setDragging] = useState(false);
  const cadreRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  const zoomIn = () => setZoom((z) => Math.min(z + 0.3, 4));
  const zoomOut = () => setZoom((z) => Math.max(z - 0.3, 0.6));
  const zoomReset = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  // Translation pour amener le marqueur au centre du cadre.
  const centrerSurJoueur = (z: number) => {
    const el = cadreRef.current;
    if (!el || !aPosition) return;
    const r = el.getBoundingClientRect();
    setPan(
      clampPan(
        {
          x: -((mx! / 100) - 0.5) * r.width * z,
          y: -((my! / 100) - 0.5) * r.height * z,
        },
        z
      )
    );
  };

  // Limite la translation pour que la carte ne sorte jamais du cadre.
  function clampPan(p: { x: number; y: number }, z: number): { x: number; y: number } {
    const el = cadreRef.current;
    if (!el) return p;
    const r = el.getBoundingClientRect();
    const maxX = Math.max(0, ((z - 1) * r.width) / 2);
    const maxY = Math.max(0, ((z - 1) * r.height) / 2);
    return {
      x: Math.max(-maxX, Math.min(maxX, p.x)),
      y: Math.max(-maxY, Math.min(maxY, p.y)),
    };
  }

  // Nouvelle position du groupe → zoom 2× + centrage automatique.
  const prevPos = useRef("");
  useEffect(() => {
    const key = `${mx ?? ""},${my ?? ""}`;
    if (key !== prevPos.current && aPosition) {
      prevPos.current = key;
      setZoom(2);
      requestAnimationFrame(() => centrerSurJoueur(2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mx, my]);

  // Glisser-déposer pour naviguer (pointer events → souris + tactile).
  const onPointerDown = (e: React.PointerEvent) => {
    if (zoom <= 1) return; // carte entière visible : rien à déplacer
    setDragging(true);
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    setPan(
      clampPan(
        {
          x: drag.current.px + (e.clientX - drag.current.x),
          y: drag.current.py + (e.clientY - drag.current.y),
        },
        zoom
      )
    );
  };
  const onPointerUp = () => {
    drag.current = null;
    setDragging(false);
  };

  return (
    <div>
      <div className="mb-2 text-sm text-amber-200 font-serif text-center">
        {mapInfo.label}
      </div>
      {lieu && (
        <div className="text-center mb-2">
          <div className="text-stone-200 text-sm font-medium">{lieu.nom}</div>
          <div className="text-xs text-stone-400">{lieu.type}</div>
        </div>
      )}
      <div
        ref={cadreRef}
        className={
          "relative bg-stone-950 rounded border border-stone-700 overflow-hidden select-none touch-none " +
          (zoom > 1 ? "cursor-grab active:cursor-grabbing" : "")
        }
        style={{ aspectRatio: `${MAP_W}/${MAP_H}` }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <div
          className={
            "absolute inset-0 " + (dragging ? "" : "transition-transform duration-200 ease-out")
          }
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
        >
          <img
            src={MAP_URL}
            alt={`Carte : ${mapInfo.label}`}
            draggable={false}
            onError={() => setErreur(true)}
            className="absolute inset-0 w-full h-full object-cover pointer-events-none"
          />
          {/* Position du groupe (patchée par le MJ) */}
          {aPosition && !erreur && (
            <div
              className="absolute"
              style={{ left: `${mx}%`, top: `${my}%` }}
            >
              <div className="relative -translate-x-1/2 -translate-y-1/2 flex flex-col items-center">
                <span className="relative flex">
                  <span className="absolute inset-0 rounded-full bg-amber-400/60 animate-ping" />
                  <span className="relative w-3 h-3 rounded-full bg-amber-400 border-2 border-amber-100 shadow-md" />
                </span>
                {lieu?.nom && (
                  <span
                    className="mt-1 px-1.5 py-0.5 rounded bg-stone-950/80 border border-amber-700/60 text-amber-300 text-[10px] font-medium whitespace-nowrap font-serif"
                    style={{ transform: `scale(${1 / zoom})` }}
                  >
                    {lieu.nom}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
        {erreur && (
          <div className="absolute inset-0 flex items-center justify-center text-center text-stone-500 text-xs italic px-6">
            Carte introuvable sur le serveur —
            redémarrez le serveur pour la copier dans data/scenarios/.
          </div>
        )}
        {!aPosition && !erreur && (
          <div className="absolute inset-x-0 bottom-0 bg-stone-950/80 text-center text-[10px] text-stone-400 py-1 px-2 italic">
            Le MJ n'a pas encore placé le groupe sur la carte.
          </div>
        )}
      </div>
      {/* Contrôles zoom — sous la carte */}
      <div className="flex justify-center items-center gap-1 mt-2">
        <button
          onClick={zoomOut}
          className="w-7 h-7 rounded bg-stone-700 hover:bg-stone-600 text-stone-200 font-bold text-sm leading-none"
          title="Dézoomer"
        >
          −
        </button>
        <button
          onClick={zoomReset}
          className="px-2 h-7 rounded bg-stone-700 hover:bg-stone-600 text-stone-300 text-[10px]"
          title="Réinitialiser"
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          onClick={zoomIn}
          className="w-7 h-7 rounded bg-stone-700 hover:bg-stone-600 text-stone-200 font-bold text-sm leading-none"
          title="Zoomer"
        >
          +
        </button>
        {aPosition && (
          <button
            onClick={() => centrerSurJoueur(zoom)}
            className="px-2 h-7 rounded bg-amber-800 hover:bg-amber-700 text-stone-100 text-[10px] font-medium"
            title="Centrer sur le groupe"
          >
            📍
          </button>
        )}
        {urlAtlas && (
          <a
            href={urlAtlas}
            target="_blank"
            rel="noreferrer"
            className="px-2 h-7 flex items-center rounded bg-stone-700 hover:bg-stone-600 text-sky-300 text-[10px]"
            title={`Voir ${nomAtlas} sur l'atlas interactif AideDD (internet)`}
          >
            🌐 Atlas
          </a>
        )}
      </div>
      <p className="text-[10px] text-stone-500 mt-1.5 text-center italic">
        Glissez pour naviguer · marqueur doré = position du groupe.
      </p>
    </div>
  );
}
