
const WS_URL = "ws://127.0.0.1:8765";

const statusEl = document.getElementById("status");

const store = new CaptionStore({
  session: "L1",
  historySeconds: 60
});

const ui = new CaptionUI(store);

function numericClientId() {
  const key = "liverecall-client-id";

  let value = localStorage.getItem(key);

  if (value === null) {
    value = String(Math.floor(Math.random() * 1000000) + 1);
    localStorage.setItem(key, value);
  }

  return Number(value);
}

const connection = new ConnectionManager(WS_URL, {
  getClientId: numericClientId,

  getResumeState: () => store.getResumeState(),

  onHistory: (items) => {
    store.mergeHistory(items);
    ui.render();
  },

  onCaption: (message) => {
    if (store.applyUpdate(message)) {
      ui.render();
    }
  },

  onStatusChange: (status) => {
    statusEl.textContent = status;
    ui.setConnectionStatus(status);
  }
});

connection.connect();

window.addEventListener("beforeunload", () => {
  connection.disconnect();
  ui.dispose();
  store.dispose();
});
