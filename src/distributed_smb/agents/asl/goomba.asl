//Belief base
alive.
mario_x(MX).
mario_y(MY).
goomba_x(GX).
goomba_y(GY).

mario_near :-
    goomba_y(GY) == mario_y(MY) &&
    math.abs(goomba_x(GX) - mario_x(MX)) < 5.


//Initial goal
!patrol.

//Plans
+!patrol : mario_near
    <- !kill_mario.

+!patrol : obstacle_ahead
    <- turn_around;
       !patrol.

+!patrol : true
    <- move_forward;
       !patrol.

-alive : 
    <- .kill_agent.

+!kill_mario : 
    <- .peint("Mario is dead")