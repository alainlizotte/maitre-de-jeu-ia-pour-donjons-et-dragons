// Client WebSocket — une seule connexion persistante par partie.
// Reconnexion automatique avec backoff exponentiel (1s → 5s plafonné).
// La découverte du bon endpoint WS discrimine ss:// vs ws:// selon location.protocol.
// Heartbeat applicatif : ping toutes les 20 s, reconnexion forcée si aucun
// pong depuis 45 s — sans lui, une TCP morte laissait l'écran gelé jusqu'à
// un rafraîchissement manuel de la page.

import type { WsMessage } from "./types";

export type WsHandler = (msg: WsMessage) => void;
export type OpenHandler = () => void;

const PING_INTERVAL_MS = 20_000;
const PONG_WATCHDOG_MS = 45_000;

export class ChatSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers = new Set<WsHandler>();
  private openHandlers = new Set<OpenHandler>();
  private retries = 0;
  private manualClose = false;
  private pingTimer: number | null = null;
  private lastPong = 0;

  constructor(partie_id: string) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    // En dev Vite (5173), le proxy /ws route vers 8000 ; en prod, même origine.
    this.url = `${proto}//${window.location.host}/ws/${partie_id}`;
  }

  on(h: WsHandler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  /** Notifié à CHAQUE connexion établie (initiale ET reconnexions) — le hook
   *  s'en sert pour re-envoyer le join (parties protégées : les registres
   *  d'authentification sont par connexion côté serveur). */
  onOpen(h: OpenHandler): () => void {
    this.openHandlers.add(h);
    return () => this.openHandlers.delete(h);
  }

  connect(): void {
    this.manualClose = false;
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.retries = 0;
      this.lastPong = Date.now();
      this.startHeartbeat();
      this.openHandlers.forEach((h) => h());
    };
    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WsMessage;
        if (msg.type === "pong") {
          this.lastPong = Date.now();
          return; // heartbeat — pas de routing vers l'UI
        }
        this.handlers.forEach((h) => h(msg));
      } catch {
        /* payload non JSON — ignoré */
      }
    };
    this.ws.onclose = () => {
      this.stopHeartbeat();
      if (this.manualClose) return;
      // Backoff exponentiel plafonné à 5 secondes.
      const delay = Math.min(1000 * 2 ** this.retries, 5000);
      this.retries += 1;
      setTimeout(() => this.connect(), delay);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.pingTimer = window.setInterval(() => {
      if (this.ws?.readyState !== WebSocket.OPEN) return;
      // Watchdog : pas de pong depuis 45 s → la connexion est morte
      // (send() sur un TCP fantôme ne lève PAS immédiatement). On ferme
      // volontairement → onclose déclenche la reconnexion + re-join.
      if (Date.now() - this.lastPong > PONG_WATCHDOG_MS) {
        this.ws.close();
        return;
      }
      this.send({ type: "ping" });
    }, PING_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  send(payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
      this.retries = 0; // un envoi réussi réinitialise le backoff.
    }
  }

  join(player: string, password?: string, personnage?: string): void {
    const payload: Record<string, unknown> = { type: "join", player };
    // Mot de passe requis pour les parties protégées (sinon ignoré côté serveur).
    if (password) payload.password = password;
    // Personnage choisi dans le menu déroulant de l'accueil — le serveur
    // enregistre le PJ dans l'état de la partie.
    if (personnage) payload.personnage = personnage;
    this.send(payload);
  }

  say(player: string, text: string): void {
    this.send({ type: "say", player, text });
  }

  close(): void {
    this.manualClose = true;
    this.stopHeartbeat();
    this.ws?.close();
    this.ws = null;
  }
}
