//Belief base
direction(right).

// Tunables (ticks are simulation ticks: 60 per second).
sighting_refresh(60).   // re-announce a sighting that is still valid this often
sighting_ttl(180).      // a sighting older than this is ignored by who receives it
encircle_range(600).    // how far (px) a Goomba comes from to close the player in
same_height(32).        // max vertical distance (px) to be "at the player's height"

// flank(Side, Sender, SenderX, PX): agent Sender (standing at SenderX) sees a
// player at PX, at my height, and the player stands between Sender and me,
// on my Side: walking that way closes it in. Sender comes from the source
// annotation that spade_bdi adds to every received message.
flank(left, Sender, SenderX, PX) :-
    perception(Now) & player_spotted(PX, PY, SenderX, Seen)[source(Sender)]
    & sighting_ttl(TTL)
    & Now - Seen < TTL
    & position(X, Y) & same_height(H) & PY - Y < H & Y - PY < H
    & SenderX < PX & PX < X
    & encircle_range(Range) & X - PX < Range.

flank(right, Sender, SenderX, PX) :-
    perception(Now) & player_spotted(PX, PY, SenderX, Seen)[source(Sender)]
    & sighting_ttl(TTL)
    & Now - Seen < TTL
    & position(X, Y) & same_height(H) & PY - Y < H & Y - PY < H
    & X < PX & PX < SenderX
    & encircle_range(Range) & PX - X < Range.

// ---------------------------------------------------------------------------
// Movement. Each new game perception produces exactly one decision: the first
// applicable plan below wins, so the order of the plans is their priority.
// The heading is bound in the context, i.e. taken from the same snapshot
// that triggered the plan: a newer perception may already have changed the
// direction belief by the time the action runs.
// ---------------------------------------------------------------------------

// --- The way to the player is blocked by a void or a wall: wait there ---
// (before the safety plans: standing still is always safe, while turning
// away and coming back would make the Goomba jitter at the edge)
+perception(Sequence) : player_near(Side) & path_blocked(Side)
    <- .hold.

+perception(Sequence) : not player_near(_) & flank(Side, _, _, _) & path_blocked(Side)
    <- .hold.

// --- Safety first: never walk into a void or a wall ---
+perception(Sequence) : void_ahead & direction(Direction)
    <- .turn_around(Direction).

+perception(Sequence) : obstacle_ahead & direction(Direction)
    <- .turn_around(Direction).

// --- Behaviour kill_mario: a player at the same height is near ---
// Turn towards it (even if it is behind).
+perception(Sequence) : player_near(Side)
    <- .chase(Side).

// --- Behaviour encircle: close in a player another agent is facing ---
// Reached only when this Goomba does not see the player itself (otherwise
// kill_mario above applies): it acts on what another agent told it.
+perception(Sequence) : flank(Side, Sender, SenderX, PX)
    <- .encircle(Side, Sender, SenderX, PX).

// --- Behaviour patrol ---
+perception(Sequence) : clear_ahead & direction(right)
    <- .move_right.

+perception(Sequence) : clear_ahead & direction(left)
    <- .move_left.

// ---------------------------------------------------------------------------
// Communication. A Goomba that sees a player tells every other agent where
// the player is and where it stands itself:
//     player_spotted(PlayerX, PlayerY, SenderX, Tick)
// The sighting is refreshed while the player stays in sight and withdrawn
// (untell) when it is lost. announced(...) remembers what was told, because
// untell must name exactly the belief to remove.
// ---------------------------------------------------------------------------

+player_sighted(PX, PY, Seen) : not announced(_, _, _, _)
    <- !announce(PX, PY, Seen).

+player_sighted(PX, PY, Seen)
    : announced(_, _, _, Last) & sighting_refresh(Every) & Seen - Last >= Every
    <- !withdraw;
       !announce(PX, PY, Seen).

-player_near(Side)
    <- !withdraw.

+!announce(PX, PY, Seen) : peers(Peers) & position(X, _)
    <- .send(Peers, tell, player_spotted(PX, PY, X, Seen));
       +announced(PX, PY, X, Seen).

+!withdraw : announced(PX, PY, X, Seen) & peers(Peers)
    <- .send(Peers, untell, player_spotted(PX, PY, X, Seen));
       -announced(PX, PY, X, Seen).

+!withdraw
    <- true.
