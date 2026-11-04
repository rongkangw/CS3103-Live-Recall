class ConnectionManager {
  constructor(url, callbacks = {}) {
    this.url = url;
    this.callbacks = callbacks;

    this.ws = null;
    this.reconnectTimer = null;
    this.reconnectAttempts = 0;

    this.stopped = false;

    // reconnect starts at 1 second and increases up to 10 seconds
    this.baseDelay = 1000;
    this.maxDelay = 10000;
  }

  connect() {
    // dont try to connect again if the manager has been stopped
    if (this.stopped) return;

    // avoid creating another connection if one is already active or connecting
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
       this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    this.setStatus("connecting");

    const socket = new WebSocket(this.url);
    this.ws = socket;

    // the connection is ready, so reset the retry counter
    socket.addEventListener("open", () => {
      if (this.ws !== socket) return;

      this.reconnectAttempts = 0;
      this.setStatus("connected");

      // let the server know who we are and where to resume from
      this.sendHello(socket);
    });

    // handle incoming messages from the server
    socket.addEventListener("message", (event) => {
      if (this.ws !== socket) return;

      let message;

      // messages are expected to be json, so ignore anything that cant be parsed
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        console.error("Invalid WebSocket message:", error);
        return;
      }

      this.handleMessage(message);
    });

    // if the connection closes, try to reconnect unless it was intentional
    socket.addEventListener("close", () => {
      if (this.ws !== socket) return;

      this.ws = null;

      if (this.stopped) {
        this.setStatus("disconnected");
        return;
      }

      this.setStatus("reconnecting");
      this.scheduleReconnect();
    });

    // log socket errors for debugging
    socket.addEventListener("error", (error) => {
      console.error("WebSocket error:", error);
    });
  }

  sendHello(socket) {
    // get the last known position so the server can send any missing data
    const resume = this.callbacks.getResumeState();

    const message = {
      type: "CLIENT_HELLO",
      client_id: this.callbacks.getClientId(),
      session: "L1",
      last_segment: resume.last_segment,
      last_revision: resume.last_revision,
      history_seconds: 60
    };

    // only send the handshake once the socket is open
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
    }
  }

  handleMessage(message) {
    // ignore empty or invalid messages
    if (!message || typeof message !== "object") return;

    // pass each message to the appropriate callback
    switch (message.type) {
      case "HISTORY":
        this.callbacks.onHistory(message.items ?? []);
        break;

      case "CAPTION_UPDATE":
        this.callbacks.onCaption(message);
        break;

      default:
        // useful for spotting new message types added by the server
        console.warn("Unknown server message:", message.type);
    }
  }

  scheduleReconnect() {
    // make sure we dont schedule multiple reconnect attempts at once
    if (this.stopped || this.reconnectTimer !== null) return;

    // increase the delay after each failed attempt, up to the maximum
    const delay = Math.min(
      this.baseDelay * (2 ** this.reconnectAttempts),
      this.maxDelay
    );

    this.reconnectAttempts++;

    console.log(`Reconnecting in ${delay / 1000}s`);

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  setStatus(status) {
    // keep the rest of the app updated when the connection status changes
    this.callbacks.onStatusChange(status);
  }

  disconnect() {
    // mark the connection as intentionally stopped so it wont reconnect
    this.stopped = true;

    // clear any pending reconnect attempt
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    // close the current socket if there is one
    if (this.ws) {
      const socket = this.ws;
      this.ws = null;
      socket.close();
    }

    this.setStatus("disconnected");
  }
}

// make the class accessible from other scripts
window.ConnectionManager = ConnectionManager;