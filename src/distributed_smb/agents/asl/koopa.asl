//Belief base
direction(right).

// Tunables.
patrol_area(150).    // half width (px) of the area patrolled around home(X)
dive_cone(32).       // |dx| (px) within which the player is "right below"
altitude_margin(8).  // tolerance (px) on the cruise altitude

// home(X) and cruise(Y) are set by the agent from its first perception,
// i.e. its spawn point: the centre of the patrol area and the flight altitude.

// in_area(X): X lies inside the patrol area.
in_area(X) :- home(H) & patrol_area(A) & X > H - A & X < H + A.

// player_below: a player of the area is right under the Lakitu
// (y grows downwards, so "below" means a greater y).
player_below :-
    player_seen(PX, PY) & in_area(PX) & position(X, Y) & dive_cone(C)
    & PX - X < C & X - PX < C & PY > Y.

// below_cruise: the Lakitu is lower than its flight altitude (after a dive).
below_cruise :- position(_, Y) & cruise(CY) & altitude_margin(M) & Y > CY + M.

// ---------------------------------------------------------------------------
// Each new game perception produces exactly one decision: the first
// applicable plan below wins, so the order of the plans is their priority.
// The heading is bound in the context, i.e. taken from the same snapshot
// that triggered the plan.
// ---------------------------------------------------------------------------

// --- Behaviour kill_mario ---
// The player is right below: dive on it, but never into the ground (the
// game applies no collision to enemies, the Lakitu has to stop by itself).
+perception(Sequence) : player_below & not near_ground
    <- .dive.

// After a dive (player hit, dodged, or ground reached): climb back up.
+perception(Sequence) : below_cruise
    <- .rise.

// The player is in the area but not below: fly over it.
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
