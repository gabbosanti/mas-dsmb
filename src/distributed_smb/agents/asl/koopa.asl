//Belief base
direction(right).

// Tunables.
patrol_area(150).   // half width (px) of the area patrolled around home(X)
overhead(8).        // |dx| (px) below which the Lakitu is above the player

// in_area(X): X lies inside the patrol area. home(X) is set by the agent
// from its first perception, i.e. its spawn point.
in_area(X) :- home(H) & patrol_area(A) & X > H - A & X < H + A.

// ---------------------------------------------------------------------------
// Each new game perception produces exactly one decision: the first
// applicable plan below wins, so the order of the plans is their priority.
// The heading is bound in the context, i.e. taken from the same snapshot
// that triggered the plan.
// ---------------------------------------------------------------------------

// --- Behaviour kill_mario: a player is inside the patrol area ---
// Above it already: stay there.
+perception(Sequence)
    : player_seen(PX, _) & in_area(PX) & position(X, _) & overhead(D)
      & PX - X < D & X - PX < D
    <- .hold.

+perception(Sequence) : player_seen(PX, _) & in_area(PX) & position(X, _) & PX < X
    <- .chase(left).

+perception(Sequence) : player_seen(PX, _) & in_area(PX) & position(X, _) & PX > X
    <- .chase(right).

// --- Behaviour patrol: fly back and forth inside the area ---
+perception(Sequence)
    : direction(right) & position(X, _) & home(H) & patrol_area(A) & X >= H + A
    <- .turn_around(right).

+perception(Sequence)
    : direction(left) & position(X, _) & home(H) & patrol_area(A) & X <= H - A
    <- .turn_around(left).

+perception(Sequence) : direction(right)
    <- .move_right.

+perception(Sequence) : direction(left)
    <- .move_left.
