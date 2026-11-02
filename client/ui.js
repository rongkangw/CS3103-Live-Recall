/** DOM rendering only. The connection manager feeds CaptionStore and calls render(). */
class CaptionUI {
  constructor(store, root = document) {
    this.store = store;
    this.root = root;
    this.seconds = 30;
    this.live = root.getElementById("live");
    this.recall = root.getElementById("recall");
    this.rows = { live: new Map(), recall: new Map() };
    this.timeFormat = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
    this.buttons = Array.from(root.querySelectorAll("button[data-seconds]"));
    this.onWindowClick = (event) => this.selectRecall(Number(event.currentTarget.dataset.seconds));
    for (const button of this.buttons) button.addEventListener("click", this.onWindowClick);
    this.openButton = root.getElementById("open-recall");
    this.closeButton = root.getElementById("close-recall");
    this.recallPanel = root.getElementById("recall-panel");
    this.onOpen = () => {
      this.recallPanel.hidden = false;
      this.openButton.setAttribute("aria-expanded", "true");
      this.selectRecall(30);
      root.getElementById("recall-heading").focus();
    };
    this.onClose = () => {
      this.recallPanel.hidden = true;
      this.openButton.setAttribute("aria-expanded", "false");
      this.openButton.focus();
    };
    this.onRecallKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        this.onClose();
      }
    };
    this.openButton.addEventListener("click", this.onOpen);
    this.closeButton.addEventListener("click", this.onClose);
    this.recallPanel.addEventListener("keydown", this.onRecallKeydown);
    this.render();
    // Keep recall and empty states current even during silence or disconnection.
    this.timer = setInterval(() => this.render(), 1000);
  }

  selectRecall(seconds) {
    if (![15, 30, 60].includes(seconds)) return;
    this.seconds = seconds;
    this.render();
  }

  _renderRows(container, rows, captions) {
    const keys = new Set(captions.map((c) => JSON.stringify([c.session, c.segment])));
    for (const [key, row] of rows) {
      if (!keys.has(key)) { row.remove(); rows.delete(key); }
    }
    captions.forEach((caption, index) => {
      const key = JSON.stringify([caption.session, caption.segment]);
      let row = rows.get(key);
      if (!row) {
        row = document.createElement("li");
        row.className = "caption-row";
        const meta = document.createElement("div");
        meta.className = "caption-meta";
        const time = document.createElement("time");
        const state = document.createElement("span");
        state.className = "caption-state";
        const text = document.createElement("p");
        text.className = "caption-text";
        meta.append(time);
        if (container === this.live) meta.append(state);
        row.append(meta, text);
        rows.set(key, row);
      }
      if (row._caption !== caption) {
        row.dataset.final = String(caption.final);
        row.dataset.segment = String(caption.segment);
        row.dataset.session = caption.session;
        const time = row.querySelector("time");
        time.dateTime = new Date(caption.source_ts * 1000).toISOString();
        time.textContent = this.timeFormat.format(caption.source_ts * 1000);
        const state = row.querySelector(".caption-state");
        if (state) state.textContent = caption.final ? "Final" : "Provisional";
        // Incoming text must never be interpreted as HTML.
        row.querySelector(".caption-text").textContent = caption.text;
        row._caption = caption;
      }
      // Preserve the DOM node for revisions and leave unchanged rows in place.
      if (container.children[index] !== row) container.insertBefore(row, container.children[index] || null);
    });
  }

  render() {
    const live = this.store.getLiveCaptions();
    const recall = this.store.getRecall(this.seconds);
    const followLive = this.live.scrollHeight - this.live.scrollTop - this.live.clientHeight < 64;
    this._renderRows(this.live, this.rows.live, live);
    this._renderRows(this.recall, this.rows.recall, recall);
    if (followLive) this.live.scrollTop = this.live.scrollHeight;
    this.root.getElementById("live-empty").hidden = live.length > 0;
    this.root.getElementById("recall-empty").hidden = recall.length > 0;
    this.root.getElementById("recall-empty").textContent =
      `No finalized captions in the last ${this.seconds} seconds. Completed captions will appear here.`;
    this.root.getElementById("recall-window").textContent = `Last ${this.seconds} seconds`;
    for (const button of this.buttons) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.seconds) === this.seconds));
    }
  }

  setConnectionStatus(status) {
    const element = this.root.getElementById("status");
    element.textContent = status;
    element.dataset.state = status;
  }

  dispose() {
    clearInterval(this.timer);
    for (const button of this.buttons) button.removeEventListener("click", this.onWindowClick);
    this.openButton.removeEventListener("click", this.onOpen);
    this.closeButton.removeEventListener("click", this.onClose);
    this.recallPanel.removeEventListener("keydown", this.onRecallKeydown);
  }
}
