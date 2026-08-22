# Weekly Retro Tool — Project Scope

A weekly, facilitated retrospective tool for a single project team.

## Core model
- **Scope:** one project, whole team participates.
- **Focus:** the project (progress, blockers, health) — not individual people.
- **Format:** Start / Stop / Continue cards.
- **Identity:** names shown by default; each card has an optional anonymous toggle.
- **Architecture:** full multiplayer — everyone joins from their own device with live sync.

## Session flow
The facilitator advances phases manually via a single "Next phase" button. Everyone's view updates to the current phase.

1. **Write** — each person adds Start/Stop/Continue cards; sees only their own cards.
2. **Reveal** — facilitator triggers; all cards appear at once for everyone.
3. **Cluster** — anyone can drag cards into groups on a shared, live board.
4. **Vote** — each person gets 3 votes, cast on individual cards, stackable on one card.
5. **Discuss & record** — cards sorted by vote count; team logs decisions + action items.
6. **Post-meeting** — facilitator can upload audio, video, or a transcript. No built-in recording in v1.

## What full multiplayer requires
- **Real-time sync** — cards, clusters, votes, and phase changes update live across devices (WebSockets, or a service like Firebase / Supabase / Liveblocks).
- **Sessions** — a join code or link so the right people land in the same retro.
- **Identity** — lightweight (name on join) or real accounts.
- **Persistent storage** — cards, votes, decisions, and uploads survive refresh.
- **Facilitator role** — controls who can advance phases and trigger the reveal.
- **File uploads** — storage for post-meeting media.
- **Conflict handling** — e.g. two people dragging the same card; vote limits enforced server-side.

## Decisions locked in
| Question | Decision |
|---|---|
| Who contributes | All team members |
| Feedback focus | The project itself |
| Format | Start / Stop / Continue |
| Anonymity | Names by default, opt-in anonymous per card |
| Pre-reveal visibility | Own cards only |
| Reveal | All cards at once, facilitator-triggered |
| Phase advancement | Manual (facilitator-driven) |
| Clustering | Anyone can drag |
| Voting | 3 votes per person, on individual cards, stackable |
| Output | Decisions + action items |
| Recording | Upload audio/video/transcript post-meeting; no built-in recording in v1 |
| Join model | Full multiplayer, own devices, live sync |

## Open questions for later
- Identity: name-on-join vs. real accounts?
- Sync backend choice (Firebase / Supabase / Liveblocks / custom WebSockets)?
- Does clustering affect the vote tally, or is it discussion-only? (Currently: votes are per-card; clusters guide discussion.)
- Persistence horizon — keep past retros as history, or one-off sessions?
- Facilitator assignment — first to join, or explicitly chosen?

## Superseded (earlier async-log scope, now dropped)
An earlier direction scoped an async, always-on, fully-anonymous feedback log (free-text, storage-only, grouped by week). This was replaced by the facilitated retro above.
