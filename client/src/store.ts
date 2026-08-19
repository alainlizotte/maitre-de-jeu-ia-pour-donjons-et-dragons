// Store Zustand central — fil de discussion, état persisté + participants.
// Sert d'accumulateur des événements WS (delta streaming, tool_event, dm final).

import { create } from "zustand";
import type { ChatMessage, PartyState, ToolEvent } from "./api/types";

interface PartyStore {
  // -- Infos partie ------------------------------------------------------- //
  partie_id: string | null;
  player: string;
  setPlayer: (p: string) => void;
  setPartieId: (id: string | null) => void;

  // -- État persistant (mirroir PartyState côté backend) ----------------- //
  state: PartyState | null;
  setState: (s: PartyState) => void;

  // -- Fil de discussion -------------------------------------------------- //
  messages: ChatMessage[];
  thinking: boolean;
  participants: string[];

  addMessage: (m: ChatMessage) => void;
  appendDelta: (streamId: string, text: string) => void;
  finalizeStream: (streamId: string, content: string, toolEvents: ToolEvent[], image?: string) => void;
  setThinking: (v: boolean) => void;
  setParticipants: (p: string[]) => void;
  addParticipant: (p: string) => void;
  reset: () => void;
}

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

export const useParty = create<PartyStore>((set) => ({
  partie_id: null,
  player: "",
  setPlayer: (p) => set({ player: p }),
  setPartieId: (id) => set({ partie_id: id }),

  state: null,
  setState: (s) => set({ state: s }),

  messages: [],
  thinking: false,
  participants: [],

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

  reset: () => set({ messages: [], state: null, thinking: false, participants: [] }),
}));

export { uid };
