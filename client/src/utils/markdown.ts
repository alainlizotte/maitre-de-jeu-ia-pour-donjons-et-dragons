// Rendu Markdown sécurisé via `marked`. Désactive explicitement HTML inline
// pour éviter l'injection XSS depuis une narration LLM.

import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

export function renderMarkdown(text: string): string {
  if (!text) return "";
  // marked ne sanitize pas natif ; on échappe les balises HTML avant rendu,
  // puis on parse le markdown restant (**gras**, listes, titres).
  const esc = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return marked.parse(esc) as string;
}
