import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";

interface DonjonCellule {
  id?: string;
  visite?: boolean;
  courant?: boolean;
  type?: string;
  /** Côté serveur : dict {"nord": true, ...} — on tolère aussi string[]. */
  portes?: string[] | Record<string, boolean>;
  x?: number;
  y?: number;
}

interface DonjonState {
  id: string | null;
  salles_visitees?: string[];
  portes_bloquees?: string[];
  grille?: DonjonCellule[];
  /** Position courante [x, y] (format serveur). */
  courant?: number[];
  current_room?: string;
  current_x?: number;
  current_y?: number;
}

interface DungeonViewProps {
  sendSay?: (text: string) => void;
}

/** Normalise les portes (dict {nord: true} ou tableau) en liste de directions. */
function listePortes(p?: string[] | Record<string, boolean>): string[] {
  if (!p) return [];
  if (Array.isArray(p)) return p;
  return Object.entries(p)
    .filter(([, v]) => Boolean(v))
    .map(([k]) => k);
}

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;
const ZOOM_PAS = 1.25;

export function DungeonView({ sendSay }: DungeonViewProps) {
  const state = useParty((s) => s.state);
  const partieId = useParty((s) => s.partie_id);
  const donjon = state?.donjon as DonjonState | undefined;
  const grille = donjon?.grille as DonjonCellule[] | undefined;
  const visitees = donjon?.salles_visitees ?? [];

  // Auto-actualisation : rechargement de l'img toutes les 4 s via un
  // cache-buster dans l'URL (le serveur re-rend le SVG depuis l'état live,
  // Cache-Control: no-store).
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const i = setInterval(() => setTick((t) => t + 1), 4000);
    return () => clearInterval(i);
  }, []);

  // --- Visionneuse : auto-fit + zoom manuel ------------------------------- //
  // zoom = 1 → la carte tient ENTIÈREMENT dans la case (auto-fit) ;
  // +/- multiplient ce niveau de base (pan par scroll quand dépassé).
  const [zoom, setZoom] = useState(1);
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  // Dimensions naturelles du SVG — corrigées au chargement (l'auto-fit
  // se recalcule alors, y compris quand le donjon s'agrandit).
  const [nat, setNat] = useState({ w: 216, h: 152 });

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const update = () =>
      setBox({ w: el.clientWidth || 1, h: el.clientHeight || 1 });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Largeur affichée : fit (contient dans la case) × zoom, bornée.
  const fitW = Math.min(box.w - 8, ((box.h - 8) * nat.w) / nat.h);
  const dispW = Math.round(Math.max(40, fitW * zoom));

  const carteUrl = partieId
    ? `/api/parties/${partieId}/carte-donjon.svg?v=${tick}`
    : null;

  // Salle courante : position [x, y] de l'état (le flag `courant` des
  // cellules n'est pas toujours renseigné côté serveur).
  const curX = Array.isArray(donjon?.courant) ? donjon!.courant![0] : donjon?.current_x;
  const curY = Array.isArray(donjon?.courant) ? donjon!.courant![1] : donjon?.current_y;
  const salleCourante =
    grille?.find((c) => c.courant) ??
    grille?.find((c) => c.x === curX && c.y === curY);

  if (!donjon?.id) {
    return (
      <div className="text-center text-stone-500 text-sm italic">
        Aucun donjon actif.
      </div>
    );
  }

  // Portes disponibles de la salle courante
  const portes = listePortes(salleCourante?.portes);
  const goDir = (dir: string) => {
    if (sendSay) sendSay(`Je vais au ${dir}`);
  };

  const ordreDirs = ["ouest", "nord", "sud", "est"] as const;
  const fleches: Record<string, string> = {
    nord: "↑",
    sud: "↓",
    est: "→",
    ouest: "←",
  };

  const btnZoom =
    "w-6 h-6 rounded bg-stone-800/90 hover:bg-stone-700 border border-stone-600 " +
    "text-stone-200 text-xs leading-none flex items-center justify-center transition-colors";

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* En-tête : titre + compteur de salles */}
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm text-amber-200 font-serif">Carte du donjon</span>
        <span className="text-[10px] text-stone-500">
          {visitees.length} salle{visitees.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Visionneuse : auto-fit dans la case, pan par scroll si zoomé */}
      <div ref={boxRef} className="relative flex-1 min-h-36 max-h-72 mb-2">
        <div className="absolute inset-0 overflow-auto rounded shadow border border-stone-800 bg-stone-950/60">
          <img
            src={carteUrl ?? undefined}
            alt="Carte donjon SVG"
            className="block m-auto"
            style={{ width: dispW }}
            onLoad={(e) => {
              const el = e.target as HTMLImageElement;
              // Ré-affiche une image potentiellement masquée par un onError
              // antérieur (404 transitoire pendant l'absence de donjon).
              el.style.display = "";
              if (el.naturalWidth > 0) {
                setNat((prev) =>
                  prev.w === el.naturalWidth && prev.h === el.naturalHeight
                    ? prev
                    : { w: el.naturalWidth, h: el.naturalHeight }
                );
              }
            }}
            onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
          />
        </div>
        {/* Contrôles zoom (hors zone scrollable) */}
        <div className="absolute top-1 right-1 z-10 flex items-center gap-1">
          <button
            className={btnZoom}
            title="Zoom arrière"
            onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z / ZOOM_PAS))}
          >
            −
          </button>
          <span className="px-1 h-6 rounded bg-stone-900/90 border border-stone-700 text-[9px] text-stone-400 flex items-center tabular-nums">
            {Math.round(zoom * 100)}%
          </span>
          <button
            className={btnZoom}
            title="Zoom avant"
            onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z * ZOOM_PAS))}
          >
            +
          </button>
          <button
            className={btnZoom}
            title="Ajuster à la case (100 %)"
            onClick={() => setZoom(1)}
          >
            ⤢
          </button>
        </div>
      </div>

      {/* Pad directionnel compact : une seule rangée */}
      {sendSay && portes.length > 0 && (
        <div className="flex gap-1 mb-2 shrink-0">
          {ordreDirs.map((d) =>
            portes.includes(d) ? (
              <button
                key={d}
                onClick={() => goDir(d)}
                className="flex-1 h-7 rounded bg-stone-700 hover:bg-amber-700 text-stone-200 text-xs font-medium transition-colors"
              >
                {fleches[d]} {d.charAt(0).toUpperCase() + d.slice(1)}
              </button>
            ) : (
              <div
                key={d}
                className="flex-1 h-7 rounded bg-stone-900 border border-stone-800 flex items-center justify-center text-stone-700 text-xs"
                title="Passage inconnu"
              >
                ·
              </div>
            )
          )}
        </div>
      )}
      {/* Pied : salle courante uniquement — les illustrations de salles sont
          dans la galerie « Scènes » du bas. */}
      <div className="text-xs text-stone-400 truncate shrink-0">
        {salleCourante ? (
          <>
            <span className="text-red-400">●</span> {salleCourante.type ?? "salle"}
          </>
        ) : (
          "Position actuelle"
        )}
      </div>
    </div>
  );
}
