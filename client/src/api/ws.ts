// Client WebSocket — une seule connexion persistante par partie.
// Reconnexion automatique avec backoff exponentiel (1s → 5s plafonné).
// La découverte du bon endpoint WS discrimine ss:// vs ws:// selon location.protocol.

import type { WsMessage } from "./types";

export type WsHandler = (msg: WsMessage) => void;

export class ChatSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers = new Set<WsHandler>();
  private retries = 0;
  private manualClose = false;

  constructor(partie_id: string) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    // En dev Vite (5173), le proxy /ws route vers 8000 ; en prod, même origine.
    this.url = `${proto}//${window.location.host}/ws/${partie_id}`;
  }

  on(h: WsHandler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  connect(): void {
    this.manualClose = false;
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WsMessage;
        this.handlers.forEach((h) => h(msg));
      } catch {
        /* payload non JSON — ignoré */
      }
    };
    this.ws.onclose = () => {
      if (this.manualClose) return;
      // Backoff exponentiel plafonné à 5 secondes.
      const delay = Math.min(1000 * 2 ** this.retries, 5000);
      this.retries += 1;
      setTimeout(() => this.connect(), delay);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  send(payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
      this.retries = 0; // un envoi réussi réinitialise le backoff.
    }
  }

  join(player: string, password?: string): void {
    const payload: Record<string, unknown> = { type: "join", player };
    // Mot de passe requis pour les parties protégées (sinon ignoré côté serveur).
    if (password) payload.password = password;
    this.send(payload);
  }

  say(player: string, text: string): void {
    this.send({ type: "say", player, text });
  }

  close(): void {
    this.manualClose = true;
    this.ws?.close();
    this.ws = null;
  }
}
