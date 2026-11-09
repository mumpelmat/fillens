/** Hintergrundabfragen ersetzen keine gerade bearbeitete Seite oder Bilddaten. */
export function refreshLearningPage<T extends {items: {id: string}[]; pending_count: number}>(
  displayed: T | null, incoming: T, background: boolean,
): {page: T; hasUpdates: boolean} {
  if (!background || !displayed?.items.length) return {page: incoming, hasUpdates: false};
  return {
    page: displayed,
    hasUpdates: incoming.pending_count > displayed.pending_count ||
      incoming.items.some(item => !displayed.items.some(old => old.id === item.id)),
  };
}
