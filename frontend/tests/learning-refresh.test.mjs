import test from 'node:test';
import assert from 'node:assert/strict';
import {refreshLearningPage} from '../src/lib/learning-refresh.ts';

test('Neue Bilder verdrängen weder die bearbeitete Seite noch geänderte Bilddaten', () => {
  const editing = {items: [{id:'old', fill:42}], pending_count:65};
  const newer = {items: [{id:'new', fill:null}, {id:'old', fill:55}], pending_count:66};
  const background = refreshLearningPage(editing, newer, true);
  assert.equal(background.page, editing);
  assert.equal(background.page.items[0].fill, 42);
  assert.equal(background.hasUpdates, true);
  assert.equal(refreshLearningPage(editing, newer, false).page, newer);
});
test('Leere Listen füllen sich automatisch; gleiche Antworten lösen keine Meldung aus', () => {
  const empty = {items:[], pending_count:0};
  const first = {items:[{id:'first'}], pending_count:1};
  assert.equal(refreshLearningPage(empty, first, true).page, first);
  assert.equal(refreshLearningPage(first, structuredClone(first), true).hasUpdates, false);
});
