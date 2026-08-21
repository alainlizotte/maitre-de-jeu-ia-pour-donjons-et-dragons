// Carte du monde — rendu fidèle de la Côte des Épées (Faerûn ouest) :
// littoral de l'Échine du Monde (nord) à la Calimshan (sud), mer des Épées,
// fleuves Delimbiyr et Chionthar, régions et villes aux positions canoniques.

import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";

interface Ville {
  nom: string;
  x: number;
  y: number;
  grande?: boolean; // métropole (point + libellé plus visibles)
}

// Positions approximatives conformes à la carte officielle « Sword Coast ».
const VILLES: Ville[] = [
  { nom: "Luskan", x: 19, y: 13 },
  { nom: "Neverwinter", x: 16, y: 24 },
  { nom: "Waterdeep", x: 20, y: 38, grande: true },
  { nom: "Daggerford", x: 26, y: 43 },
  { nom: "Secomber", x: 33, y: 52 },
  { nom: "Baldur's Gate", x: 29, y: 64, grande: true },
  { nom: "Athkatla", x: 28, y: 76, grande: true },
  { nom: "Murann", x: 23, y: 80 },
  { nom: "Darromar", x: 39, y: 87 },
  { nom: "Calimport", x: 33, y: 121, grande: true },
  { nom: "Silverymoon", x: 55, y: 29, grande: true },
  { nom: "Suzail", x: 64, y: 71, grande: true },
];

// Littoral : points (x, y) du nord au sud — la terre est à l'est (droite).
const COTE = [
  [13, 0], [13.5, 5], [12.5, 8], [15, 11], [14, 16], [14.8, 21], [13.9, 26],
  [15.5, 32], [17.2, 38], [18.5, 44], [21.5, 50], [22.8, 55], [21.5, 60],
  [23.5, 64], [25, 69], [26.5, 74], [27.8, 79], [25.5, 83], [27, 87],
  [29.5, 92], [31.5, 97], [32, 102], [30.5, 107], [31.5, 113], [33, 119],
  [36, 124], [42, 129], [50, 134], [58, 140],
];

const COTE_PATH =
  "M " + COTE.map(([x, y]) => `${x},${y}`).join(" L ") + " L 100,140 L 100,0 Z";

// Chaînes de montagnes (arcs de petits triangles).
function Montagnes({ points, opacite = 1 }: { points: [number, number][]; opacite?: number }) {
  return (
    <g opacity={opacite}>
      {points.map(([x, y], i) => (
        <path
          key={i}
          d={`M ${x - 1.1},${y} L ${x},${y - 1.5} L ${x + 1.1},${y} Z`}
          fill="#5b5142"
          stroke="#7a6c55"
          strokeWidth="0.15"
        />
      ))}
    </g>
  );
}

function arc(x0: number, y0: number, x1: number, y1: number, n: number, jitter = 0.8): [number, number][] {
  const pts: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    const j = i === 0 || i === n - 1 ? 0 : (Math.sin(i * 12.9898) * jitter);
    pts.push([x0 + (x1 - x0) * t + j * 0.4, y0 + (y1 - y0) * t + j * 0.5]);
  }
  return pts;
}

// Massifs forestiers (bouquets de conifères stylisés).
function Foret({ x, y, n = 18 }: { x: number; y: number; n?: number }) {
  const arbres: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const a = (i * 2.399) % (Math.PI * 2); // spirale dorée → répartition naturelle
    const r = 1.6 * Math.sqrt(i);
    arbres.push([x + r * Math.cos(a), y + r * Math.sin(a) * 0.7]);
  }
  return (
    <g opacity="0.85">
      {arbres.map(([tx, ty], i) => (
        <path key={i} d={`M ${tx},${ty} l 0.7,1.4 l -1.4,0 Z`} fill="#3d5a45" />
      ))}
    </g>
  );
}

export function WorldMap() {
  const state = useParty((s) => s.state);
  const lieu = state?.lieu;
  const [zoom, setZoom] = useState(1);

  // Le MJ patche position_x/position_y sur 0–100 ; la carte est haute de 140.
  const mx = lieu?.position_x;
  const my = lieu?.position_y != null ? lieu.position_y * 1.4 : null;

  const zoomIn = () => setZoom((z) => Math.min(z + 0.3, 3));
  const zoomOut = () => setZoom((z) => Math.max(z - 0.3, 0.6));
  const zoomReset = () => setZoom(1);

  // Auto-centre sur le joueur à 2× quand la position change.
  const prevPos = useRef<string>("");
  useEffect(() => {
    const key = `${mx ?? ""},${my ?? ""}`;
    if (key !== prevPos.current && mx != null) {
      prevPos.current = key;
      setZoom(2);
    }
  }, [mx, my]);

  // Calcul du viewBox centré sur le joueur si disponible, sinon centre carte.
  const cx = mx ?? 50;
  const cy = my ?? 70;
  const baseW = 100;
  const baseH = 140;
  const vw = baseW / zoom;
  const vh = baseH / zoom;
  const vx = Math.max(0, Math.min(cx - vw / 2, baseW - vw));
  const vy = Math.max(0, Math.min(cy - vh / 2, baseH - vh));

  return (
    <div>
      <div className="mb-2 text-sm text-amber-200 font-serif text-center">
        Faerûn — Côte des Épées
      </div>
      {lieu && (
        <div className="text-center mb-2">
          <div className="text-stone-200 text-sm font-medium">{lieu.nom}</div>
          <div className="text-xs text-stone-400">{lieu.type}</div>
        </div>
      )}
      {/* Contrôles zoom */}
      <div className="flex justify-center gap-1 mb-2">
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
        {mx != null && (
          <button
            onClick={() => { setZoom(2); }}
            className="px-2 h-7 rounded bg-amber-800 hover:bg-amber-700 text-stone-100 text-[10px] font-medium"
            title="Centrer sur le groupe"
          >
            📍
          </button>
        )}
      </div>
      <div className="relative bg-stone-800 rounded border border-stone-700 overflow-hidden" style={{ aspectRatio: "100/140" }}>
        <svg viewBox={`${vx} ${vy} ${vw} ${vh}`} className="w-full h-full transition-all duration-300 ease-out">
          {/* Océan */}
          <rect width="100" height="140" fill="#16222e" />
          {/* Vagues discrètes */}
          <g stroke="#24384a" strokeWidth="0.2" fill="none">
            {[14, 30, 46, 62, 78, 94, 110, 126].map((y) => (
              <path key={y} d={`M 2,${y} q 2,-0.8 4,0 t 4,0 t 4,0`} />
            ))}
          </g>

          {/* Continent */}
          <path d={COTE_PATH} fill="#332d24" stroke="#8a7d5b" strokeWidth="0.35" />
          {/* Liseré littoral */}
          <path d={COTE_PATH} fill="none" stroke="#c4a96a" strokeWidth="0.12" opacity="0.6" />

          {/* Désert d'Anauroch + désert de Calim */}
          <g fill="#4a4433" opacity="0.85">
            <path d="M 62,14 Q 82,8 99,16 L 99,50 Q 84,56 66,44 Q 58,28 62,14 Z" />
            <path d="M 40,104 Q 52,100 58,108 Q 56,120 46,124 Q 38,118 40,104 Z" />
          </g>
          <g stroke="#6b614a" strokeWidth="0.15">
            <path d="M 66,22 l 1.5,0.7 M 74,30 l 1.5,0.7 M 84,24 l 1.5,0.7 M 90,36 l 1.5,0.7 M 72,40 l 1.5,0.7 M 46,110 l 1.5,0.7 M 52,116 l 1.5,0.7" />
          </g>

          {/* Chaînes de montagnes */}
          <Montagnes points={arc(6, 2.5, 30, 7, 9)} />
          <Montagnes points={arc(34, 10, 48, 17, 5)} />
          <Montagnes points={arc(17, 17, 24, 20, 4)} />
          <Montagnes points={arc(27, 45, 34, 49, 4)} />
          <Montagnes points={arc(37, 79, 48, 83, 5)} />
          <Montagnes points={arc(57, 66, 66, 69, 4)} />
          <Montagnes points={arc(52, 96, 62, 101, 4)} opacite={0.8} />

          {/* Forêts */}
          <Foret x={41} y={37} n={26} />
          <Foret x={40} y={87} n={14} />
          <Foret x={30} y={57} n={8} />
          <Foret x={70} y={54} n={8} />

          {/* Fleuves */}
          <g stroke="#3d6a8a" strokeWidth="0.35" fill="none" opacity="0.9">
            {/* Delimbiyr : Hauts Bois → Daggerford → mer au sud de Waterdeep */}
            <path d="M 39,36 Q 32,39 27,43 Q 21,42 17,37" />
            {/* Chionthar : Cormyr → Baldur's Gate */}
            <path d="M 62,70 Q 46,66 30,64" />
            {/* Mirar : montagnes → Luskan */}
            <path d="M 26,10 Q 22,12 19,13" />
          </g>

          {/* Libellés de régions */}
          <g fontFamily="Georgia, serif" fontStyle="italic" fill="#8a7d6b">
            <text x="9" y="52" fontSize="3" textAnchor="middle" transform="rotate(-78 9 52)">MER DES ÉPÉES</text>
            <text x="80" y="31" fontSize="3" textAnchor="middle">Anauroch</text>
            <text x="41" y="48.5" fontSize="2.6" textAnchor="middle">Hauts Bois</text>
            <text x="42" y="91.5" fontSize="2.4" textAnchor="middle">Forêt de Tethir</text>
            <text x="40" y="117" fontSize="2.6" textAnchor="middle">Calim</text>
            <text x="16" y="110" fontSize="2.8" textAnchor="middle" transform="rotate(-70 16 110)">MER BRILLANTE</text>
            <text x="52" y="20" fontSize="2.6" textAnchor="middle" fill="#7a8a96">ÉCHINE DU MONDE</text>
            <text x="62" y="75" fontSize="2.4" textAnchor="middle">Cormyr</text>
            <text x="36" y="74" fontSize="2.4" textAnchor="middle">Amn</text>
            <text x="35" y="94" fontSize="2.4" textAnchor="middle">Tethyr</text>
            <text x="37" y="128" fontSize="2.6" textAnchor="middle">Calimshan</text>
            <text x="52" y="34" fontSize="2.4" textAnchor="middle">Marches d'Argent</text>
          </g>

          {/* Villes */}
          {VILLES.map((v) => (
            <g key={v.nom}>
              <circle cx={v.x} cy={v.y} r={v.grande ? 1.1 : 0.75} fill="#c4a96a" stroke="#1a1a23" strokeWidth="0.2" />
              <text
                x={v.x + 1.6}
                y={v.y + 0.9}
                fontSize={v.grande ? 2.7 : 2.2}
                fill={v.grande ? "#e8d5a3" : "#a89a80"}
                fontWeight={v.grande ? "bold" : "normal"}
                fontFamily="Georgia, serif"
              >
                {v.nom}
              </text>
            </g>
          ))}

          {/* Position du groupe (patchée par le MJ) */}
          {mx != null && my != null && (
            <g>
              <circle cx={mx} cy={my} r="2.2" fill="#f59e0b" />
              <circle cx={mx} cy={my} r="4" fill="none" stroke="#f59e0b" strokeWidth="0.5" opacity="0.5">
                <animate attributeName="r" from="3" to="6" dur="2s" repeatCount="indefinite" />
                <animate attributeName="opacity" from="0.5" to="0" dur="2s" repeatCount="indefinite" />
              </circle>
              <text
                x={mx}
                y={my + 5}
                textAnchor="middle"
                fontSize="2.6"
                fill="#fbbf24"
                fontWeight="bold"
                fontFamily="Georgia, serif"
              >
                {lieu?.nom}
              </text>
            </g>
          )}

          {/* Rose des vents */}
          <g transform="translate(90,10)" stroke="#8a7d6b" fill="#8a7d6b">
            <line x1="0" y1="3" x2="0" y2="-6" strokeWidth="0.5" />
            <path d="M 0,-7 L 1.2,-4 L 0,-4.8 L -1.2,-4 Z" strokeWidth="0.2" />
            <text x="0" y="-8" fontSize="2.6" textAnchor="middle" stroke="none">N</text>
          </g>
        </svg>
      </div>
      <p className="text-[10px] text-stone-500 mt-1 text-center italic">
        La position du groupe est indiquée par le marqueur doré.
      </p>
    </div>
  );
}
