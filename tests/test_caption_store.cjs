const { test } = require("node:test");
const assert = require("node:assert/strict");
const { CaptionStore } = require("../client/caption_store.js");

const caption = (overrides = {}) => ({
  session: "L1", segment: 1, revision: 1, source_ts: 1000,
  final: false, text: "TCP uses", ...overrides,
});
const makeStore = () => new CaptionStore({ clock: () => 1000 });

test("revisions replace one row; stale, identical and post-final revisions are rejected", () => {
  const store = makeStore();
  assert.equal(store.applyUpdate(caption()), true);
  assert.equal(store.applyUpdate(caption({ revision: 2, text: "TCP uses cumulative" })), true);
  assert.equal(store.applyUpdate(caption()), false);
  assert.equal(store.applyUpdate(caption({ revision: 2, text: "duplicate" })), false);
  assert.equal(store.applyUpdate(caption({ revision: 3, final: true, text: "Complete" })), true);
  assert.equal(store.applyUpdate(caption({ revision: 4 })), false);
  assert.equal(store.applyUpdate(caption({ revision: 5, final: true })), false);
  assert.equal(store.getLiveCaptions().length, 1);
  assert.equal(store.getLiveCaptions()[0].text, "Complete");
});

test("history merges overlap and out-of-order finals without regressing resume state", () => {
  const store = makeStore();
  assert.deepEqual(store.getResumeState(), { last_segment: 0, last_revision: 0 });
  store.applyUpdate(caption({ segment: 10, revision: 9 }));
  assert.equal(store.getResumeState().last_segment, 0);
  const final = caption({ segment: 3, revision: 2, final: true });
  store.applyUpdate(final);
  assert.equal(store.mergeHistory([final, caption({ final: true }), null]), 1);
  assert.deepEqual(store.getLiveCaptions().map((c) => c.segment), [1, 3, 10]);
  assert.deepEqual(store.getResumeState(), { last_segment: 3, last_revision: 2 });
});

test("same segment in different sessions has independent captions and resume state", () => {
  const store = makeStore();
  store.applyUpdate(caption({ final: true }));
  store.applyUpdate(caption({ session: "L2", revision: 3, final: true }));
  assert.equal(store.getLiveCaptions()[0].revision, 1);
  assert.equal(store.getLiveCaptions("L2")[0].revision, 3);
  assert.equal(store.getResumeState("L2").last_revision, 3);
});

test("recall uses source seconds, includes window boundaries and excludes provisionals", () => {
  const store = makeStore();
  [940, 970, 985, 1000].forEach((source_ts, index) => {
    store.applyUpdate(caption({ segment: index + 1, source_ts, final: true }));
  });
  store.applyUpdate(caption({ segment: 5 }));
  assert.deepEqual(store.getRecall(60).map((c) => c.segment), [1, 2, 3, 4]);
  assert.deepEqual(store.getRecall().map((c) => c.segment), [2, 3, 4]);
  assert.deepEqual(store.getRecall(15).map((c) => c.segment), [3, 4]);
  assert.deepEqual(store.getRecall(0), []);
});

test("pruning preserves resume state and expired history cannot resurrect captions", () => {
  let now = 1000;
  const store = new CaptionStore({ clock: () => now });
  const final = caption({ final: true });
  store.applyUpdate(final);
  now = 1061;
  assert.deepEqual(store.getLiveCaptions(), []);
  assert.equal(store.mergeHistory([final]), 0);
  assert.deepEqual(store.getResumeState(), { last_segment: 1, last_revision: 1 });
});

test("malformed messages are ignored and caller mutations cannot alter stored state", () => {
  const store = makeStore();
  for (const invalid of [null, {}, caption({ source_ts: NaN }), caption({ final: 1 }),
    caption({ segment: 0 }), caption({ revision: true }), caption({ text: null })]) {
    assert.equal(store.applyUpdate(invalid), false);
  }
  const input = caption({ final: true });
  store.applyUpdate(input);
  input.text = "changed";
  const rows = store.getLiveCaptions();
  Reflect.set(rows[0], "text", "also changed");
  rows.pop();
  const resume = store.getResumeState();
  resume.last_segment = 100;
  assert.equal(store.getLiveCaptions()[0].text, "TCP uses");
  assert.equal(store.getResumeState().last_segment, 1);
  assert.throws(() => store.getRecall(61), RangeError);
});

test("recall orders recovered finals by source timestamp, with a stable segment tie-break", () => {
  const store = makeStore();
  store.mergeHistory([
    caption({ segment: 1, source_ts: 999, final: true }),
    caption({ segment: 4, source_ts: 980, final: true }),
    caption({ segment: 3, source_ts: 980, final: true }),
    caption({ segment: 2, source_ts: 950, final: true }),
  ]);
  assert.deepEqual(store.getRecall(60).map((c) => c.segment), [2, 3, 4, 1]);
  assert.deepEqual(store.getRecall(30).map((c) => c.segment), [3, 4, 1]);
  assert.deepEqual(store.getRecall(15).map((c) => c.segment), [1]);
  store.dispose();
});

test("idle pruning retains the exact 60-second boundary and preserves resume state", (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let now = 1000;
  const store = new CaptionStore({ clock: () => now });
  t.after(() => store.dispose());
  store.applyUpdate(caption({ final: true }));
  now = 1060;
  t.mock.timers.tick(1000);
  assert.equal(store._captions.size, 1);
  now = 1060.001;
  t.mock.timers.tick(1000);
  // Inspect storage directly: a read must not be what triggers pruning here.
  assert.equal(store._captions.size, 0);
  assert.deepEqual(store.getResumeState(), { last_segment: 1, last_revision: 1 });
  assert.equal(store.mergeHistory([caption({ final: true })]), 0);
});

test("a long-running lecture retains only the rolling window", (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let now = 1000;
  const store = new CaptionStore({ clock: () => now });
  t.after(() => store.dispose());
  for (let segment = 1; segment <= 600; segment += 1) {
    now += 1;
    store.applyUpdate(caption({ segment, source_ts: now, final: true }));
    t.mock.timers.tick(1000);
  }
  assert.equal(store._captions.size, 61);
  assert.equal(store.getRecall(60).length, 61);
  assert.equal(store.getRecall(30).length, 31);
  assert.equal(store.getRecall(15).length, 16);
  assert.equal(store.getResumeState().last_segment, 600);
});

test("disposing the store stops background pruning", (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let now = 1000;
  const store = new CaptionStore({ clock: () => now });
  store.applyUpdate(caption({ final: true }));
  store.dispose();
  store.dispose();
  now = 1100;
  t.mock.timers.tick(1000);
  assert.equal(store._captions.size, 1);
  // Reads still enforce the history window after the timer is stopped.
  assert.deepEqual(store.getRecall(60), []);
});
