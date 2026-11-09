import assert from "node:assert/strict";
import test from "node:test";
import { loadHistories } from "../src/lib/history-loader.ts";

test("Höchstens vier parallele Anfragen, unabhängig von Abschlussreihenfolge und Einzelfehlern", async () => {
  let active = 0, maximum = 0;
  const results = new Map();
  const ids = Array.from({ length: 13 }, (_, n) => String(n));
  await loadHistories(ids, async (id) => {
    maximum = Math.max(maximum, ++active);
    await new Promise((resolve) => setTimeout(resolve, Number(id) % 3));
    active--;
    if (id === "3") throw new Error("Behälter nicht erreichbar");
    return { container: id };
  }, (id, value) => results.set(id, value), new AbortController().signal);
  assert.equal(maximum, 4);
  assert.equal(results.size, ids.length);
  assert.deepEqual(results.get("3"), { error: "Behälter nicht erreichbar" });
  for (const id of ids.filter((id) => id !== "3")) assert.equal(results.get(id).data.container, id);
});

test("Reiter-/Zeitraumwechsel verwirft alte Antworten und startet keine weiteren alten Anfragen", async () => {
  const old = new AbortController();
  const resolvers = [];
  const published = [];
  const pending = loadHistories(["a", "b", "c", "d", "e"], () => new Promise((resolve) => resolvers.push(resolve)),
    (id, result) => published.push([id, result]), old.signal);
  assert.equal(resolvers.length, 4);
  old.abort();
  await loadHistories(["neu"], async () => 42, (id, result) => published.push([id, result]), new AbortController().signal);
  resolvers.forEach((resolve) => resolve("veraltet"));
  await pending;
  assert.equal(resolvers.length, 4);
  assert.deepEqual(published, [["neu", { data: 42 }]]);
});
