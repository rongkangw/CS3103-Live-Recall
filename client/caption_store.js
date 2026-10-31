class CaptionStore {
  /** clock returns Unix seconds, matching caption_source.py's source_ts. */
  constructor({ session = "L1", historySeconds = 60, clock = () => Date.now() / 1000 } = {}) {
    if (typeof session !== "string" || !session) {
      throw new TypeError("session must be a non-empty string");
    }
    if (!Number.isFinite(historySeconds) || historySeconds < 60) {
      throw new RangeError("historySeconds must be at least 60");
    }
    if (typeof clock !== "function") throw new TypeError("clock must be a function");
    this.session = session;
    this.historySeconds = historySeconds;
    this.clock = clock;
    this._captions = new Map();
    this._resume = new Map();
  }

  _prune(now) {
    for (const [key, caption] of this._captions) {
      if (caption.source_ts < now - this.historySeconds) this._captions.delete(key);
    }
  }

  /** Return true only when the caption changes stored state. Invalid input is ignored. */
  applyUpdate(caption) {
    if (!caption || typeof caption !== "object" ||
        typeof caption.session !== "string" || !caption.session ||
        !Number.isSafeInteger(caption.segment) || caption.segment < 1 ||
        !Number.isSafeInteger(caption.revision) || caption.revision < 1 ||
        !Number.isFinite(caption.source_ts) ||
        typeof caption.final !== "boolean" || typeof caption.text !== "string") {
      return false;
    }

    const now = this.clock();
    this._prune(now);
    // Expired replayed messages must not reappear after pruning.
    if (caption.source_ts < now - this.historySeconds) return false;
    const key = JSON.stringify([caption.session, caption.segment]);
    const current = this._captions.get(key);
    if (current && (current.final || caption.revision <= current.revision)) return false;

    // Copy only protocol fields so callers cannot mutate the stored state.
    const stored = Object.freeze({
      type: "CAPTION_UPDATE",
      session: caption.session,
      segment: caption.segment,
      revision: caption.revision,
      source_ts: caption.source_ts,
      final: caption.final,
      text: caption.text,
    });
    this._captions.set(key, stored);

    if (stored.final) {
      const resume = this._resume.get(stored.session);
      if (!resume || stored.segment > resume.last_segment ||
          (stored.segment === resume.last_segment && stored.revision > resume.last_revision)) {
        this._resume.set(stored.session, {
          last_segment: stored.segment,
          last_revision: stored.revision,
        });
      }
    }
    return true;
  }

  /** Merge HISTORY.items through the same revision rules; return the accepted count. */
  mergeHistory(items) {
    if (!Array.isArray(items)) return 0;
    let accepted = 0;
    for (const caption of items) {
      if (caption && caption.final === true && this.applyUpdate(caption)) accepted += 1;
    }
    return accepted;
  }

  /** Latest retained revision of each segment, in lecture order. */
  getLiveCaptions(session = this.session) {
    this._prune(this.clock());
    return Array.from(this._captions.values())
      .filter((caption) => caption.session === session)
      .sort((a, b) => a.segment - b.segment);
  }

  /** Final captions from the requested local window; no network request is made. */
  getRecall(seconds = 30, session = this.session) {
    if (!Number.isFinite(seconds) || seconds < 0 || seconds > this.historySeconds) {
      throw new RangeError("seconds must be between 0 and historySeconds");
    }
    const now = this.clock();
    return this.getLiveCaptions(session).filter((caption) =>
      seconds > 0 && caption.final &&
      caption.source_ts >= now - seconds && caption.source_ts <= now);
  }

  /** CLIENT_HELLO fields: provisional updates never advance this cursor. */
  getResumeState(session = this.session) {
    return { ...(this._resume.get(session) || { last_segment: 0, last_revision: 0 }) };
  }
}

// Also allow dependency-free tests with Node; browsers use the class directly.
if (typeof module !== "undefined" && module.exports) module.exports = { CaptionStore };
