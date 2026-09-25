// Carte du monde — image adaptée à l'univers de la quête en cours.
// La carte ZOOM automatiquement et se CENTRE sur la position du groupe
// (`positions_joueurs`, % de carte : x = ouest→est, y = nord→sud — mêmes
// repères que VILLES_REPERES et les `depart` des manifestes). Un bouton
// bascule vers la carte entière ; un clic ouvre le plein écran (fermeture
// par clic ou Échap). Épingles = groupe + PJ nommés.

import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";

// ── Lookup univers → carte par défaut ──────────────────────────────────── //
// Clé = ID d'univers dans scenarios_catalogue.json ; valeur = URL de la
// carte principale + dimensions natives (pour le ratio d'affichage).
interface MapInfo { url: string; w: number; h: number; label: string; atlas?: string }

const FALLBACK_MAP: MapInfo = {
  url: "/data/cartes/Sword-Coast-Map.jpg",
  w: 4945, h: 3146, label: "La Côte des Épées",
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

// ── Zoom + centrage sur le groupe ──────────────────────────────────────── //
// Facteur de zoom appliqué en vue « groupe » (1 = carte entière).
// Capacité maximale du zoom : 5x (demande beta test — la vue groupe montre
// les villes repères voisines autour du groupe).
const AUTO_ZOOM = 5;

interface Box { dx: number; dy: number; dw: number; dh: number }

/** Boîte réellement occupée par l'image dans son conteneur (object-cover
 *  rogne, object-contain letterboxe) : indispensable pour placer un marqueur
 *  — et calculer le centrage — au bon endroit pixel. Recalculé au
 *  redimensionnement. */
function useImageBox(
  ref: React.RefObject<HTMLDivElement>,
  natW: number,
  natH: number,
  mode: "cover" | "contain",
): Box | null {
  const [box, setBox] = useState<Box | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !natW || !natH) return;
    const calc = () => {
      const cw = el.clientWidth;
      const ch = el.clientHeight;
      if (!cw || !ch) return;
      const scale =
        mode === "cover"
          ? Math.max(cw / natW, ch / natH)
          : Math.min(cw / natW, ch / natH);
      const dw = natW * scale;
      const dh = natH * scale;
      setBox({ dx: (cw - dw) / 2, dy: (ch - dh) / 2, dw, dh });
    };
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, natW, natH, mode]);
  return box;
}

/** Épingle de position (groupe ou PJ nommé) — position ÉCRAN calculée avec
 *  la même transformation que l'image (tx/ty + zoom), donc elle reste
 *  posée sur son lieu quand la carte zoome/se déplace. */
function Marker({
  nom,
  x,
  y,
  box,
  z,
  tx,
  ty,
}: {
  nom: string;
  x: number;
  y: number;
  box: Box;
  z: number;
  tx: number;
  ty: number;
}) {
  const left = tx + z * (box.dx + (x / 100) * box.dw);
  const top = ty + z * (box.dy + (y / 100) * box.dh);
  return (
    <div
      className="absolute z-10 pointer-events-none"
      style={{ left: `${left}px`, top: `${top}px`, transform: "translate(-50%, -100%)" }}
    >
      <div className="flex flex-col items-center">
        <span
          className="text-lg leading-none drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]"
          title={`${nom} (${x.toFixed(0)}, ${y.toFixed(0)})`}
        >
          📍
        </span>
        <span className="px-1 rounded bg-black/65 text-amber-200 text-[9px] leading-tight whitespace-nowrap -mt-0.5">
          {nom}
        </span>
      </div>
    </div>
  );
}

/** Panneau carte (onglet OU plein écran) : image + épingles, avec zoom de
 *  recentrage sur le groupe tant que `vueEntiere` est faux. */
function VueCarte({
  mapUrl,
  alt,
  mode,
  boxRef,
  box,
  focus,
  positions,
  vueEntiere,
  erreur,
  onImgSize,
  onErreur,
  onClick,
  containerClass,
  children,
}: {
  mapUrl: string;
  alt: string;
  mode: "cover" | "contain";
  boxRef: React.RefObject<HTMLDivElement>;
  box: Box | null;
  focus: [number, number] | null;
  positions: [string, number[]][];
  vueEntiere: boolean;
  erreur: boolean;
  onImgSize: (w: number, h: number) => void;
  onErreur: () => void;
  onClick?: () => void;
  containerClass: string;
  children?: React.ReactNode;
}) {
  const z = vueEntiere || !focus || !box ? 1 : AUTO_ZOOM;
  // Translation pour amener le point du groupe au centre de l'écran :
  // le point g de la boîte devient écran = tx + z·g → tx = cw/2 − z·gx.
  let tx = 0;
  let ty = 0;
  if (box && focus && z > 1) {
    const gx = box.dx + (focus[0] / 100) * box.dw;
    const gy = box.dy + (focus[1] / 100) * box.dh;
    const cw = box.dw + 2 * box.dx;
    const ch = box.dh + 2 * box.dy;
    tx = cw / 2 - z * gx;
    ty = ch / 2 - z * gy;
  }
  return (
    <div
      ref={boxRef}
      className={containerClass}
      onClick={onClick}
      title={onClick ? "Cliquer pour afficher en plein écran" : undefined}
    >
      <div
        className="absolute inset-0"
        style={{
          transform: `translate(${tx}px, ${ty}px) scale(${z})`,
          transformOrigin: "0 0",
          transition: "transform 0.7s ease-out",
        }}
      >
        <img
          src={mapUrl}
          alt={alt}
          draggable={false}
          onError={onErreur}
          onLoad={(e) => {
            const img = e.currentTarget;
            if (img.naturalWidth && img.naturalHeight) {
              onImgSize(img.naturalWidth, img.naturalHeight);
            }
          }}
          className={
            "absolute inset-0 w-full h-full select-none pointer-events-none " +
            (mode === "cover" ? "object-cover" : "object-contain")
          }
        />
      </div>
      {box &&
        positions.map(([nom, [x, y]]) => (
          <Marker key={nom} nom={nom} x={x} y={y} box={box} z={z} tx={tx} ty={ty} />
        ))}
      {erreur && (
        <div className="absolute inset-0 flex items-center justify-center text-center text-stone-500 text-xs italic px-6">
          Carte introuvable sur le serveur —
          redémarrez le serveur pour la copier dans data/scenarios/.
        </div>
      )}
      {children}
    </div>
  );
}

export function WorldMap() {
  const state = useParty((s) => s.state);
  const lieu = state?.lieu;
  // Positions monde (% de carte) — groupe + PJ nommés.
  const positions = Object.entries(state?.positions_joueurs ?? {}).filter(
    ([, v]) => Array.isArray(v) && v.length >= 2 && isFinite(v[0]) && isFinite(v[1]),
  ) as [string, number[]][];
  // Point de centrage : le groupe en priorité, sinon la première position.
  const focusEntry = positions.find(([nom]) => nom === "groupe") ?? positions[0];
  const focus: [number, number] | null = focusEntry
    ? [focusEntry[1][0], focusEntry[1][1]]
    : null;

  // Déterminer la carte : scénario > univers > fallback
  const { universe: universeId, scenario: scenarioId } = extractIds(state?.quete?.source);
  const mapInfo = (scenarioId ? SCENARIO_MAPS[scenarioId] : undefined)
    ?? UNIVERSE_MAPS[universeId]
    ?? FALLBACK_MAP;
  const MAP_URL = mapInfo.url;
  const ATLAS_BASE = mapInfo.atlas;

  // Taille NATIVE de l'image (recalée au chargement réel — les dimensions
  // déclarées dans MapInfo ne sont qu'un fallback).
  const [nat, setNat] = useState<[number, number]>([mapInfo.w, mapInfo.h]);
  useEffect(() => setNat([mapInfo.w, mapInfo.h]), [mapInfo]);

  // Vue « carte entière » (sans zoom) — repasse automatiquement en vue
  // groupe dès que la position du groupe CHANGE.
  const [vueEntiere, setVueEntiere] = useState(false);
  const focusCle = focus ? `${focus[0]},${focus[1]}` : "";
  const prevFocus = useRef<string>("");
  useEffect(() => {
    if (prevFocus.current && focusCle && focusCle !== prevFocus.current) {
      setVueEntiere(false);
    }
    prevFocus.current = focusCle;
  }, [focusCle]);

  const [erreur, setErreur] = useState(false);
  const [pleinEcran, setPleinEcran] = useState(false);

  // Boîtes d'affichage (cover en onglet, contain en plein écran).
  const tabBoxRef = useRef<HTMLDivElement>(null);
  const fsBoxRef = useRef<HTMLDivElement>(null);
  const tabBox = useImageBox(tabBoxRef, nat[0], nat[1], "cover");
  const fsBox = useImageBox(fsBoxRef, nat[0], nat[1], "contain");

  // Atlas link (si ville connue sur carte Faerûn)
  const nomAtlas = (() => {
    if (!ATLAS_BASE) return "";
    const brut = (lieu?.nom ?? "").split(",").pop()?.trim() ?? "";
    return ATLAS_VILLES_FAERUN.has(sansAccentsMin(brut)) ? brut : "";
  })();
  const urlAtlas = nomAtlas ? `${ATLAS_BASE}@${encodeURIComponent(nomAtlas)}` : null;

  // Fermeture du plein écran au clavier (Échap).
  useEffect(() => {
    if (!pleinEcran) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPleinEcran(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [pleinEcran]);

  // Bouton « carte entière / sur le groupe » (commun aux deux vues).
  const boutonVue = focus && (
    <button
      onClick={(e) => {
        e.stopPropagation();
        setVueEntiere((v) => !v);
      }}
      className="absolute bottom-2 right-2 z-20 px-2 h-7 rounded bg-stone-900/85 border border-stone-600 text-amber-200 text-[10px] hover:bg-stone-800"
      title={vueEntiere ? "Recentrer sur le groupe" : "Voir la carte entière"}
    >
      {vueEntiere ? "🎯 Groupe" : "🗺️ Carte entière"}
    </button>
  );

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="mb-2 text-sm text-amber-200 font-serif text-center shrink-0">
        {mapInfo.label}
      </div>
      {/* Carte de l'onglet : zoomée sur le groupe, clic → plein écran */}
      <VueCarte
        mapUrl={MAP_URL}
        alt={`Carte : ${mapInfo.label}`}
        mode="cover"
        boxRef={tabBoxRef}
        box={tabBox}
        focus={focus}
        positions={positions}
        vueEntiere={vueEntiere}
        erreur={erreur}
        onImgSize={(w, h) => setNat([w, h])}
        onErreur={() => setErreur(true)}
        onClick={() => setPleinEcran(true)}
        containerClass="relative flex-1 min-h-40 rounded border border-stone-700 overflow-hidden bg-stone-950 cursor-zoom-in"
      >
        {boutonVue}
      </VueCarte>
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
            <VueCarte
              mapUrl={MAP_URL}
              alt={`Carte plein écran : ${mapInfo.label}`}
              mode="contain"
              boxRef={fsBoxRef}
              box={fsBox}
              focus={focus}
              positions={positions}
              vueEntiere={vueEntiere}
              erreur={erreur}
              onImgSize={(w, h) => setNat([w, h])}
              onErreur={() => setErreur(true)}
              containerClass="relative flex-1 min-h-0 rounded border border-stone-700 overflow-hidden bg-stone-950"
            >
              {boutonVue}
            </VueCarte>
            <p className="text-[10px] text-stone-500 mt-1.5 text-center italic shrink-0">
              Cliquez n'importe où ou Échap pour fermer.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
