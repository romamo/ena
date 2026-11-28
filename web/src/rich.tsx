import type { ReactNode } from "react";

// The model marks emphasis with Markdown's `**bold**`. The page renders that one mark as
// <strong> through React, never as HTML, and leaves every other character as typed. A run
// stays on one line, as Markdown keeps it in one paragraph, and the pattern needs no
// lookbehind, which older Safari can't parse.
const BOLD = /\*\*(\S(?:[^\n]*?\S)??)\*\*/g;

/** `text` with each `**bold**` run as <strong>; an unclosed `**` stays as typed. */
export function rich(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let from = 0;
  for (const match of text.matchAll(BOLD)) {
    if (match.index > from) nodes.push(text.slice(from, match.index));
    nodes.push(<strong key={match.index}>{match[1]}</strong>);
    from = match.index + match[0].length;
  }
  if (from < text.length) nodes.push(text.slice(from));
  return nodes;
}
