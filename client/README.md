# dnd35-client — frontend React (Vite + TypeScript + Tailwind)

Interface web riche du Maître du Jeu D&D 3.5. Remplace le fallback chat vanilla.

## Commandes

```bash
npm install            # une fois
npm run dev            # dev server sur http://localhost:5173 (proxy /api, /ws, /data → 8000)
npm run build          # build prod → ../server/static/ (servi par FastAPI à /)
npm run preview        # prévisualisation du build
```

## Architecture

```
src/
├── main.tsx            # router (react-router) + QueryClientProvider
├── App.tsx             # layout + bandeau état serveur (Ollama/RAG)
├── index.css           # Tailwind v4 + styles narration .prose-chat/.dm-bubble
├── api/
│   ├── types.ts        # DTOs miroir des payloads FastAPI
│   ├── rest.ts         # client /api (parties, health, tools, rag)
│   └── ws.ts           # ChatSocket — reconnexion auto, backoff exponentiel
├── store.ts            # store Zustand (messages, state, participants, thinking)
├── hooks/
│   └── useChatSocket.ts # branche WS ↔ store + rejeu historique au `joined`
├── utils/
│   └── markdown.ts     # rendu Markdown (marked, HTML échappé)
├── pages/
│   ├── HomePage.tsx    # pseudo + liste/création de parties
│   └── PartyPage.tsx   # layout 3 colonnes + WS + sync state REST
└── components/
    ├── StateSidebar.tsx # lieu / phase / initiative / PJ
    ├── ChatPanel.tsx    # fil de discussion + saisie + streaming delta
    └── RightSidebar.tsx # onglets Dés (visual) / Donjon (SVG) / Bestiaire
        ├── DiceRoller.tsx
        ├── DungeonView.tsx
        └── Bestiary.tsx
```

## Contrats WS (alignés sur `server/main.py`)

| `type`     | Champs clés                                        | Sens                    |
|-----------|----------------------------------------------------|-------------------------|
| `sys`     | `event` ∈ {joined, participant_joined, error}     | serveur → client        |
| `player`  | `player`, `text`                                   | écho de son propre msg  |
| `status`  | `description`, `done?`                            | "thinking…"/fin         |
| `delta`   | `text`                                             | streaming narration     |
| `tool_event` | `event` (image/log)                             | tool live               |
| `dm`      | `text`, `tool_events?`, `state_patches?`          | narration finale        |

## Notes

- `/ws/{partie_id}` : à la reconnexion, le client rejoue `history` reçu dans le
  `joined` event (persistance `chat_<id>.json` côté backend).
- `/data/` : mount FastAPI servant images généréees par ComfyUI (monstres,
  portraits, cartes). Les `_url_for` des tools préfixent par `/data/`.
- En dev, Vite (5173) proxie `/api`, `/ws` et `/data` vers `127.0.0.1:8000`.
