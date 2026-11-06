//Belief base
alive.
direction(right).

//Initial goal
!patrol.

//Plans

+!patrol : void_ahead
    <- .turn_around;
       !patrol.

+!patrol : obstacle_ahead
    <- .turn_around;
       !patrol.

+!patrol : direction(right)
    <- .move_right;
       !patrol.

+!patrol : direction(left)
    <- .move_left;
       !patrol.

-alive :
    <- .kill_agent.
