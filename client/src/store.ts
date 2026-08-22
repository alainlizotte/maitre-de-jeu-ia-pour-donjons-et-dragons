// Store Zustand central — fil de discussion, état persisté + participants.
// Sert d'accumulateur des événements WS (delta streaming, tool_event, dm final).

import { create } from "zustand";
import type { ChatMessage, EncounterMonster, PartyState, ToolEvent } from "./api/types";

const LAST_PLAYER_KEY = "dnd35.lastPlayer";
const PASSWORD_KEY = "dnd35.password";

/** Dernier pseudo saisi (persisté) ; « joueur 1 » à la première visite. */
function initialPlayer(): string {
  try {
    return localStorage.getItem(LAST_PLAYER_KEY) || "joueur 1";
  } catch {
    return "joueur 1";
  }
}

/** Mot de passe de la partie courante — sessionStorage pour survivre à un
 *  rechargement de page (le WS doit pouvoir se ré-authentifier). */
function initialPassword(): string {
  try {
    return sessionStorage.getItem(PASSWORD_KEY) || "";
  } catch {
    return "";
  }
}

interface PartyStore {
  // -- Infos partie ------------------------------------------------------- //
  partie_id: string | null;
  player: string;
  setPlayer: (p: string) => void;
  setPartieId: (id: string | null) => void;
  // Mot de passe de la partie rejointe/créée (transmis au join WS).
  password: string;
  setPassword: (p: string) => void;

  // -- État persistant (mirroir PartyState côté backend) ----------------- //
  state: PartyState | null;
  setState: (s: PartyState) => void;

  // -- Fil de discussion -------------------------------------------------- //
  messages: ChatMessage[];
  thinking: boolean;
  participants: string[];

  // -- Monstres rencontrés (galerie bas de colonne droite) --------------- //
  monsters: EncounterMonster[];
  addMonster: (m: EncounterMonster) => void;

  addMessage: (m: ChatMessage) => void;
  appendDelta: (streamId: string, text: string) => void;
  finalizeStream: (streamId: string, content: string, toolEvents: ToolEvent[], image?: string) => void;
  setThinking: (v: boolean) => void;
  setParticipants: (p: string[]) => void;
  addParticipant: (p: string) => void;

  // -- Chat d'équipe (joueurs ↔ joueurs, sans IA) ----------------------- //
  teamMessages: { player: string; text: string; ts: number }[];
  addTeamMessage: (player: string, text: string) => void;
  setTeamMessages: (msgs: { player: string; text: string }[]) => void;
  teamUnread: number;
  resetTeamUnread: () => void;
  // Panneau chat joueurs (basculent avec chat principal)
  showPlayerChat: boolean;
  togglePlayerChat: () => void;
  // Chat audio (WebRTC)
  audioEnabled: boolean;
  setAudioEnabled: (v: boolean) => void;

  reset: () => void;
}

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

export const useParty = create<PartyStore>((set) => ({
  partie_id: null,
  player: initialPlayer(),
  setPlayer: (p) => {
    set({ player: p });
    // Mémorise le dernier pseudo utilisé (redevient le défaut au rechargement).
    try {
      if (p.trim()) localStorage.setItem(LAST_PLAYER_KEY, p.trim());
    } catch {
      /* localStorage indisponible */
    }
  },
  setPartieId: (id) => set({ partie_id: id }),
  password: initialPassword(),
  setPassword: (p) => {
    set({ password: p });
    try {
      if (p) sessionStorage.setItem(PASSWORD_KEY, p);
      else sessionStorage.removeItem(PASSWORD_KEY);
    } catch {
      /* sessionStorage indisponible */
    }
  },

  state: null,
  setState: (s) => set({ state: s }),

  messages: [],
  thinking: false,
  participants: [],

  monsters: [],
  addMonster: (m) =>
    set((st) =>
      st.monsters.some((x) => x.url === m.url)
        ? { monsters: [m, ...st.monsters.filter((x) => x.url !== m.url)] }
        : { monsters: [m, ...st.monsters].slice(0, 12) },
    ),

  addMessage: (m) => set((st) => ({ messages: [...st.messages, m] })),

  appendDelta: (streamId, text) =>
    set((st) => ({
      messages: st.messages.map((m) =>
        m.id === streamId ? { ...m, content: m.content + text } : m,
      ),
    })),

  finalizeStream: (streamId, content, toolEvents, image) =>
    set((st) => ({
      messages: st.messages.map((m) =>
        m.id === streamId
          ? { ...m, content, toolEvents, image, streaming: false }
          : m,
      ),
    })),

  setThinking: (v) => set({ thinking: v }),
  setParticipants: (p) => set({ participants: p }),
  addParticipant: (p) =>
    set((st) =>
      st.participants.includes(p)
        ? st
        : { participants: [...st.participants, p] },
    ),

  // Chat d'équipe
  teamMessages: [],
  addTeamMessage: (player, text) =>
    set((st) => ({
      teamMessages: [...st.teamMessages, { player, text, ts: Date.now() }],
      teamUnread: st.teamUnread + 1,
    })),
  setTeamMessages: (msgs) =>
    set({
      teamMessages: msgs.map((m) => ({ ...m, ts: 0 })),
    }),
  teamUnread: 0,
  resetTeamUnread: () => set({ teamUnread: 0 }),

  // Panneau chat joueurs
  showPlayerChat: false,
  togglePlayerChat: () =>
    set((st) => ({ showPlayerChat: !st.showPlayerChat, teamUnread: st.showPlayerChat ? st.teamUnread : 0 })),
  // Chat audio
  audioEnabled: false,
  setAudioEnabled: (v) => set({ audioEnabled: v }),

  // Conserve player/password : ce sont des choix de session, pas de la partie.
  reset: () => set({ messages: [], state: null, thinking: false, participants: [], teamMessages: [], teamUnread: 0, showPlayerChat: false, audioEnabled: false }),
}));

export { uid };
