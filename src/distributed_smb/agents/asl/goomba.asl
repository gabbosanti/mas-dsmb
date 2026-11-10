//Belief base
alive.
direction(right).

// Each new game perception produces exactly one patrol decision.
// The heading is bound in the context, i.e. taken from the same snapshot
// that triggered the plan: a newer perception may already have changed the
// direction belief by the time the action runs.
+perception(Sequence) : void_ahead & direction(Direction)
    <- .turn_around(Direction).

+perception(Sequence) : obstacle_ahead & direction(Direction)
    <- .turn_around(Direction).

+perception(Sequence) : clear_ahead & direction(right)
    <- .move_right.

+perception(Sequence) : clear_ahead & direction(left)
    <- .move_left.
