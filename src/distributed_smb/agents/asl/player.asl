// Agent that drives the character of a disconnected player until its owner
// reconnects: it runs towards the castle, jumping over what is in the way.
//
// Beliefs set by the agent from each game snapshot:
//   on_ground, gap_ahead (the surface ends just ahead), wall_ahead,
//   enemy_ahead (a walking enemy at body height), at_goal (next to the castle),
//   position(X, Y), perception(Tick).

// ---------------------------------------------------------------------------
// Each new game perception produces exactly one decision: the first
// applicable plan below wins, so the order of the plans is their priority.
// ---------------------------------------------------------------------------

// --- Behaviour reach_castle: arrived ---
+perception(Sequence) : at_goal
    <- .wait.

// --- Behaviour jump: only from the ground, over what is in the way ---
+perception(Sequence) : on_ground & gap_ahead
    <- .jump(pit).

+perception(Sequence) : on_ground & wall_ahead
    <- .jump(wall).

+perception(Sequence) : on_ground & enemy_ahead
    <- .jump(enemy).

// --- Behaviour reach_castle: keep running right (also while in the air) ---
+perception(Sequence)
    <- .run.
