// Store Zustand central — fil de discussion, état persisté + participants.
// Sert d'accumulateur des événements WS (delta streaming, tool_event, dm final).

import { create } from "zustand";
import type { ChatMessage, EncounterMonster, PartyState, ToolEvent } from "./api/types";

const LAST_PLAYER_KEY = "dnd35.lastPlayer";
const PASSWORD_KEY = "dnd35.password";
const USER_KEY = "dnd35.utilisateur";
const PERSO_KEY = "dnd35.personnage";

/** Dernier pseudo saisi (persisté) ; « joueur 1 » à la première visite. */
function initialPlayer(): string {
  try {
    return localStorage.getItem(LAST_PLAYER_KEY) || "joueur 1";
  } catch {
    return "joueur 1";
  }
}

/** Compte connecté (persisté — survit au rechargement). */
function initialUtilisateur(): string {
  try {
    return localStorage.getItem(USER_KEY) || "";
  } catch {
    return "";
  }
}

/** Personnage choisi pour la partie courante (sessionStorage). */
function initialPersonnage(): string {
  try {
    return sessionStorage.getItem(PERSO_KEY) || "";
  } catch {
    return "";
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
  // -- Compte connecté ----------------------------------------------------- //
  utilisateur: string;
  setUtilisateur: (u: string) => void;

  // -- Infos partie ------------------------------------------------------- //
  partie_id: string | null;
  player: string;
  setPlayer: (p: string) => void;
  setPartieId: (id: string | null) => void;
  // Mot de passe de la partie rejointe/créée (transmis au join WS).
  password: string;
  setPassword: (p: string) => void;
  // Personnage choisi pour la prochaine partie (menu déroulant accueil).
  personnage: string;
  setPersonnage: (p: string) => void;

  // -- État persistant (mirroir PartyState côté backend) ----------------- //
  state: PartyState | null;
  setState: (s: PartyState) => void;
  /** Fusionne des patches à chemins pointés ("lieu.position_x"…) reçus
   *  en direct via WS — évite d'attendre le polling REST (15 s) pour
   *  voir bouger le marqueur de carte, les PV, la phase, etc. */
  applyPatches: (patches: Record<string, unknown>[]) => void;
  /** Incrémenté à chaque signal de changement serveur (pj_updated…) —
   *  PartyPage s'en sert pour re-fetch l'état REST sans attendre le poll. */
  stateRev: number;
  bumpStateRev: () => void;

  // -- Fil de discussion -------------------------------------------------- //
  messages: ChatMessage[];
  thinking: boolean;
  /** Libellé du statut MJ (« Le MJ réfléchit… », « Le MJ finalise la scène… »). */
  thinkingLabel: string;
  participants: string[];

  // -- Monstres rencontrés (galerie bas de colonne droite) --------------- //
  monsters: EncounterMonster[];
  addMonster: (m: EncounterMonster) => void;
  /** Retire un monstre de la galerie (mort au combat). Comparaison
   *  insensible à la casse/accents/tirets (nom bestiaire ↔ nom dérivé URL). */
  removeMonsterByNom: (nom: string) => void;

  // -- Scènes illustrées (salles de donjon + moments clés) ---------------- //
  scenes: EncounterMonster[];
  addScene: (s: EncounterMonster) => void;

  addMessage: (m: ChatMessage) => void;
  /** Retire un message du fil (reset de l'aperçu streamé périmé). */
  removeMessage: (id: string) => void;
  appendDelta: (streamId: string, text: string) => void;
  finalizeStream: (streamId: string, content: string, toolEvents: ToolEvent[], image?: string) => void;
  setThinking: (v: boolean, label?: string) => void;
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

/** Normalise un nom de monstre pour comparaison (casse/accents/tirets). */
function normMonsterKey(s: string): string {
  return (s || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export const useParty = create<PartyStore>((set) => ({
  utilisateur: initialUtilisateur(),
  setUtilisateur: (u) => {
    set({ utilisateur: u });
    try {
      if (u) localStorage.setItem(USER_KEY, u);
      else localStorage.removeItem(USER_KEY);
    } catch {
      /* localStorage indisponible */
    }
  },

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
  personnage: initialPersonnage(),
  setPersonnage: (p) => {
    set({ personnage: p });
    try {
      if (p) sessionStorage.setItem(PERSO_KEY, p);
      else sessionStorage.removeItem(PERSO_KEY);
    } catch {
      /* sessionStorage indisponible */
    }
  },

  state: null,
  setState: (s) => set({ state: s }),

  applyPatches: (patches) =>
    set((st) => {
      if (!st.state || !patches.length) return st;
      // Copie profonde simple (état 100 % JSON) puis merge par chemin.
      let next: Record<string, unknown>;
      try {
        next = JSON.parse(JSON.stringify(st.state));
      } catch {
        return st;
      }
      let touched = false;
      for (const p of patches) {
        if (!p) continue;
        for (const [chemin, val] of Object.entries(p)) {
          // Signaux (pas des chemins) : ignorés ici — ils déclenchent un
          // re-fetch REST via bumpStateRev côté useChatSocket.
          if (chemin === "pj_updated" || chemin === "__reset__") continue;
          const parts = chemin.split(".");
          let obj: Record<string, unknown> = next;
          for (let i = 0; i < parts.length - 1; i++) {
            const k = parts[i];
            const enfant: unknown = obj[k];
            if (enfant == null || typeof enfant !== "object") {
              // Index numérique au niveau suivant → tableau, sinon objet.
              obj[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
            }
            const cible: unknown = obj[k];
            if (cible == null || typeof cible !== "object") break;
            obj = cible as Record<string, unknown>;
          }
          obj[parts[parts.length - 1]] = val;
          touched = true;
        }
      }
      return touched ? { state: next as unknown as PartyState } : st;
    }),

  stateRev: 0,
  bumpStateRev: () => set((st) => ({ stateRev: st.stateRev + 1 })),

  messages: [],
  thinking: false,
  thinkingLabel: "Le MJ réfléchit…",
  participants: [],

  monsters: [],
  addMonster: (m) =>
    set((st) =>
      st.monsters.some((x) => x.url === m.url)
        ? { monsters: [m, ...st.monsters.filter((x) => x.url !== m.url)] }
        : { monsters: [m, ...st.monsters].slice(0, 12) },
    ),
  removeMonsterByNom: (nom) =>
    set((st) => {
      const key = normMonsterKey(nom);
      if (!key) return st;
      return {
        monsters: st.monsters.filter((m) => normMonsterKey(m.nom) !== key),
      };
    }),

  scenes: [],
  addScene: (sc) =>
    set((st) =>
      st.scenes.some((x) => x.url === sc.url)
        ? { scenes: [sc, ...st.scenes.filter((x) => x.url !== sc.url)] }
        : { scenes: [sc, ...st.scenes].slice(0, 20) },
    ),

  addMessage: (m) => set((st) => ({ messages: [...st.messages, m] })),

  removeMessage: (id) =>
    set((st) => ({ messages: st.messages.filter((m) => m.id !== id) })),

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

  setThinking: (v, label) =>
    set((st) => ({
      thinking: v,
      thinkingLabel: v ? (label || st.thinkingLabel) : st.thinkingLabel,
    })),
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
  reset: () => set({ messages: [], state: null, thinking: false, participants: [], teamMessages: [], teamUnread: 0, showPlayerChat: false, audioEnabled: false, monsters: [], scenes: [] }),
}));

export { uid };
