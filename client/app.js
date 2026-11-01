/* Socket integration. CaptionStore owns state; CaptionUI owns rendering. */

const WS_URL = "ws://127.0.0.1:8765";
const store = new CaptionStore({ session: "L1" });
const ui = new CaptionUI(store);

let ws;

function numericClientId() {
  const key = "liverecall-client-id";
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = String(Math.floor(100000 + Math.random() * 900000));
    sessionStorage.setItem(key, value);
  }
  return Number(value);
}

function connect() {
  ui.setConnectionStatus("connecting");
  ws = new WebSocket(WS_URL);

  ws.addEventListener("open", () => {
    ui.setConnectionStatus("connected");
    ws.send(JSON.stringify({
      type: "CLIENT_HELLO",
      client_id: numericClientId(),
      session: "L1",
      ...store.getResumeState(),
      history_seconds: 30
    }));
  });

  ws.addEventListener("message", (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "CAPTION_UPDATE") {
      if (store.applyUpdate(msg)) ui.render();
    } else if (msg.type === "HISTORY") {
      store.mergeHistory(msg.items);
      ui.render();
    }
  });

  ws.addEventListener("close", () => {
    ui.setConnectionStatus("disconnected");
    // TODO: automatic reconnect after a short delay, using finalized resume state. (Part D)
  });

  ws.addEventListener("error", () => {
    ui.setConnectionStatus("connection error");
  });
}

connect();
