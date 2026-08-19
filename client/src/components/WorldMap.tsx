import { useParty } from "../store";

const CITIES: { name: string; x: number; y: number }[] = [
  { name: "Baldur's Gate", x: 28, y: 48 },
  { name: "Waterdeep", x: 22, y: 58 },
  { name: "Neverwinter", x: 18, y: 32 },
  { name: "Amn", x: 30, y: 72 },
  { name: "Cormyr", x: 50, y: 42 },
  { name: "Thay", x: 72, y: 55 },
  { name: "Luskan", x: 17, y: 22 },
  { name: "Silverymoon", x: 35, y: 25 },
];

export function WorldMap() {
  const state = useParty((s) => s.state);
  const lieu = state?.lieu;

  return (
    <div>
      <div className="mb-2 text-sm text-amber-200 font-serif text-center">
        Carte du monde
      </div>
      {lieu && (
        <div className="text-center mb-2">
          <div className="text-stone-200 text-sm font-medium">{lieu.nom}</div>
          <div className="text-xs text-stone-400">{lieu.type}</div>
        </div>
      )}
      <div className="relative bg-stone-800 rounded border border-stone-700 overflow-hidden" style={{ aspectRatio: "1" }}>
        <svg viewBox="0 0 100 100" className="w-full h-full">
          <rect width="100" height="100" fill="#1a1a23" />
          {CITIES.map((c) => (
            <g key={c.name}>
              <circle cx={c.x} cy={c.y} r="1.5" fill="#c4a96a" opacity="0.6" />
              <text
                x={c.x}
                y={c.y - 3}
                textAnchor="middle"
                fontSize="2.5"
                fill="#8a7d6b"
                fontFamily="Georgia, serif"
              >
                {c.name}
              </text>
            </g>
          ))}
          {lieu?.position_x != null && lieu?.position_y != null && (
            <g>
              <circle cx={lieu.position_x} cy={lieu.position_y} r="2.5" fill="#f59e0b" />
              <circle cx={lieu.position_x} cy={lieu.position_y} r="4" fill="none" stroke="#f59e0b" strokeWidth="0.5" opacity="0.5">
                <animate attributeName="r" from="3" to="6" dur="2s" repeatCount="indefinite" />
                <animate attributeName="opacity" from="0.5" to="0" dur="2s" repeatCount="indefinite" />
              </circle>
              <text
                x={lieu.position_x}
                y={lieu.position_y + 5}
                textAnchor="middle"
                fontSize="2.8"
                fill="#fbbf24"
                fontWeight="bold"
                fontFamily="Georgia, serif"
              >
                {lieu.nom}
              </text>
            </g>
          )}
        </svg>
      </div>
    </div>
  );
}
