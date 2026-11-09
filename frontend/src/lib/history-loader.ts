/** Begrenzte Einzelabfragen: Ein Fehler beendet keine anderen Behälterabfragen. */
export async function loadHistories<T>(
  ids: string[],
  load: (id: string) => Promise<T>,
  publish: (id: string, result: { data: T } | { error: string }) => void,
  signal: AbortSignal,
) {
  let index = 0;
  async function consume() {
    while (!signal.aborted && index < ids.length) {
      const id = ids[index++];
      let result: { data: T } | { error: string };
      try {
        result = { data: await load(id) };
      } catch (error) {
        result = { error: error instanceof Error ? error.message : "Messwerte konnten nicht geladen werden." };
      }
      if (!signal.aborted) publish(id, result);
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, ids.length) }, consume));
}
