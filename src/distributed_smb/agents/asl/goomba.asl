//Belief base
direction(right).

// Each new game perception produces exactly one decision: the first
// applicable plan below wins, so the order of the plans is their priority.
// The heading is bound in the context, i.e. taken from the same snapshot
// that triggered the plan: a newer perception may already have changed the direction belief by the time the action runs.

// --- Safety first: never walk into a void or a wall ---
+perception(Sequence) : void_ahead & direction(Direction)
    <- .turn_around(Direction).

+perception(Sequence) : obstacle_ahead & direction(Direction)
    <- .turn_around(Direction).

// --- Behaviour kill_mario: a player at the same height is near ---
// Turn towards it (even if it is behind), unless that side is blocked.
+perception(Sequence) : player_near(Side) & not path_blocked(Side)
    <- .chase(Side).

// --- Behaviour patrol ---
+perception(Sequence) : clear_ahead & direction(right)
    <- .move_right.

+perception(Sequence) : clear_ahead & direction(left)
    <- .move_left.
