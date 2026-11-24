//Belief base
direction(right).

// Tunables (ticks are simulation ticks: 60 per second).
sighting_refresh(60).   // re-announce a sighting that is still valid this often

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

// --- Safety first: never walk into a void or a wall ---
+perception(Sequence) : void_ahead & direction(Direction)
    <- .turn_around(Direction).

+perception(Sequence) : obstacle_ahead & direction(Direction)
    <- .turn_around(Direction).

// --- Behaviour kill_mario: a player at the same height is near ---
// Turn towards it (even if it is behind).
+perception(Sequence) : player_near(Side)
    <- .chase(Side).

// --- Behaviour patrol ---
+perception(Sequence) : clear_ahead & direction(right)
    <- .move_right.

+perception(Sequence) : clear_ahead & direction(left)
    <- .move_left.

// ---------------------------------------------------------------------------
// Communication. A Goomba that sees a player tells the Lakitus where the
// player is and where it stands itself, so that a Lakitu on the other side
// of the player can dive to block its escape (see koopa.asl, barrier):
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

+!announce(PX, PY, Seen) : koopa_peers(Peers) & position(X, _)
    <- .send(Peers, tell, player_spotted(PX, PY, X, Seen));
       +announced(PX, PY, X, Seen).

+!withdraw : announced(PX, PY, X, Seen) & koopa_peers(Peers)
    <- .send(Peers, untell, player_spotted(PX, PY, X, Seen));
       -announced(PX, PY, X, Seen).

+!withdraw
    <- true.
