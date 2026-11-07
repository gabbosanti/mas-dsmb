//Belief base
alive.
direction(right).

// Each new game perception produces exactly one patrol decision.
+perception(Sequence) : void_ahead
    <- .turn_around.

+perception(Sequence) : obstacle_ahead
    <- .turn_around.

+perception(Sequence) : clear_ahead & direction(right)
    <- .move_right.

+perception(Sequence) : clear_ahead & direction(left)
    <- .move_left.
