# Team Contributions

| Member | Main responsibilities | Experiments/report sections | Agreed contribution % |
|---|---|---|---:|
| Rong Kang | Protocol, authoritative session state, bounded caption history, and reconnect/late-join recovery (`protocol.py`, `SessionState`, and protocol/state/recovery tests) | Architecture and invariants; T1; server-side recovery for T4 | 25% |
| Jingya | Per-client delivery, concurrent broadcasting, slow-client isolation, and bounded overload handling (`ClientState`, `client_sender()`, and client-state/live-delivery tests) | Backpressure and graceful overload; T2 and T3; before/after performance tuning | 25% |
| Kenneth | Browser caption state, local recall, and user interface (`CaptionStore`, `CaptionUI`, HTML/CSS, and browser store tests) | Browser state and UI demonstration; Requirements 1–3 | 25% |
| Shubh | Browser networking, WebSocket connection lifecycle, automatic reconnect, and client/server integration (`connection_manager.js` and `app.js`) | Reconnect and late-join evaluation for T4; packet-loss evaluation for T5; README and demo coordination | 25% |

Total contribution: **100%**.

## Shared and integration work

- Requirement 5 was integrated across Rong Kang's server-side filtering and recovery handoff, Shubh's reconnect and recovery-request transport, and Kenneth's idempotent browser merge.
- Requirement 7 was integrated across Rong Kang's duplicate filtering, Kenneth's idempotent browser state, and Shubh's end-to-end reconnect testing.
- Requirement 11 was integrated across Jingya's bounded per-client delivery state and Rong Kang's bounded retained session history.
- All members participated in integration review, end-to-end testing, and viva preparation for the parts they owned.
