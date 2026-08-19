import { useParty } from "../store";

interface DonjonCellule {
  id?: string;
  visite?: boolean;
  courant?: boolean;
  type?: string;
  portes?: string[];
  x?: number;
  y?: number;
}

interface DonjonState {
  id: string | null;
  salles_visitees?: string[];
  portes_bloquees?: string[];
  grille?: DonjonCellule[];
  current_room?: string;
  current_x?: number;
  current_y?: number;
}

function cellColor(cell: DonjonCellule): string {
  if (cell.courant) return "bg-amber-500";
  if (cell.visite) return "bg-stone-600 border-stone-500";
  return "bg-stone-800 border-stone-700";
}

export function DungeonView() {
  const state = useParty((s) => s.state);
  const donjon = state?.donjon as DonjonState | undefined;
  const grille = donjon?.grille as DonjonCellule[] | undefined;
  const visitees = donjon?.salles_visitees ?? [];

  if (!donjon?.id) {
    return (
      <div className="text-center text-stone-500 text-sm italic">
        Aucun donjon actif.
      </div>
    );
  }

  if (!grille || grille.length === 0) {
    return (
      <div className="text-center">
        <div className="mb-2 text-sm text-amber-200 font-serif">Carte du donjon</div>
        {donjon.id && (
          <img
            src={`/data/cartes/${donjon.id}.svg`}
            alt="Carte donjon"
            className="mx-auto max-h-72 rounded shadow"
            onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
          />
        )}
        <div className="mt-2 text-xs text-stone-400">
          Salles visitées : {visitees.length}
        </div>
      </div>
    );
  }

  const cellsByPos = new Map<string, DonjonCellule>();
  let maxX = 0;
  let maxY = 0;
  for (const cell of grille) {
    const x = cell.x ?? 0;
    const y = cell.y ?? 0;
    cellsByPos.set(`${x},${y}`, cell);
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }

  const rows: (DonjonCellule | null)[][] = [];
  for (let y = 0; y <= maxY; y++) {
    const row: (DonjonCellule | null)[] = [];
    for (let x = 0; x <= maxX; x++) {
      row.push(cellsByPos.get(`${x},${y}`) ?? null);
    }
    rows.push(row);
  }

  return (
    <div>
      <div className="mb-2 text-sm text-amber-200 font-serif text-center">
        Carte du donjon
      </div>
      <img
        src={`/data/cartes/${donjon.id}.svg`}
        alt="Carte donjon SVG"
        className="mx-auto max-h-48 rounded shadow mb-3"
        onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
      />
      <div className="flex justify-center">
        <div className="inline-grid gap-px bg-stone-800 p-1 rounded border border-stone-700"
          style={{ gridTemplateColumns: `repeat(${maxX + 1}, 1.5rem)` }}
        >
          {rows.map((row, ry) =>
            row.map((cell, rx) => (
              <div
                key={`${rx}-${ry}`}
                className={`w-6 h-6 rounded-sm border flex items-center justify-center text-[8px] ${cell ? cellColor(cell) : "bg-transparent border-transparent"}`}
                title={cell?.id ?? ""}
              >
                {cell?.courant ? "◆" : cell?.visite ? "·" : ""}
              </div>
            ))
          )}
        </div>
      </div>
      <div className="mt-2 text-xs text-stone-400 text-center">
        {visitees.length} salle{visitees.length !== 1 ? "s" : ""} visitée{visitees.length !== 1 ? "s" : ""}
        {" · "}
        <span className="text-amber-400">◆</span> position actuelle
      </div>
    </div>
  );
}
