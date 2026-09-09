# ISE Project: Intelligent Multi-Agent System for Distributed Super Mario Bros

This project aims to extend a project developed for the "Distributed Systems" course by introducing intelligent and autonomous agents into an existing implementation of _Super Mario Bros_.

The main objective is to investigate how autonomous agents can reason about their environment, pursue goals and coordinate their actions in a dynamic multi-agent environment. The project will focus on using intelligent agents to address problems that cannot be effectively solved through simple predefined or rule-based behaviors.

The game will include multiple autonomous agents representing different entities, primarily enemy agents such as Goombas and Koopas. Agents may have different capabilities, goals and beliefs about the environment, and their behavior will depend on the current game state and on the actions and information of other agents.

A central problem addressed by the project will be **multi-agent coordination in a dynamic environment**. Rather than independently following predefined behaviors, groups of enemy agents should be able to coordinate their actions to pursue common objectives. For example, multiple enemies could coordinate their movements to intercept Mario, surround him, or exploit information shared by other agents. The agents should be able to adapt their behavior when the environment changes or when other agents do not behave as expected.

The project will investigate the following capabilities:

- maintaining beliefs about relevant aspects of the game environment;
- pursuing goals according to the agent's role and current situation;
- selecting appropriate intentions when multiple goals are possible;
- reasoning about the consequences of their decisions;
- communicating relevant information to other agents;
- updating beliefs based on information received from other agents;
- coordinating actions with other agents;
- adapting their behavior when the environment or the behavior of other agents changes.

The project will investigate the use of the **BDI (Belief-Desire-Intention) architecture** to model agent deliberation and autonomous decision-making. BDI will not be used simply to implement individual reactive behaviors, such as deciding whether a Goomba should attack Mario. Instead, it will be used to model agents that have multiple possible objectives and must select and pursue intentions in an environment containing other autonomous agents.

An additional application of autonomous agents will be investigated in the context of the distributed nature of the original project. If a human player disconnects or becomes unavailable, an autonomous agent could take control of the corresponding character. The objective is to provide a form of **graceful degradation and autonomous substitution**, allowing the game to continue without requiring another human player to take over.

In this scenario, the autonomous agent would inherit the relevant state of the disconnected player and autonomously determine its behavior according to the current game situation. The agent could, for example, attempt to follow the other players, avoid enemies and obstacles, and contribute to the progress of the game. When the original player reconnects, control could potentially be transferred back to the human player.

This mechanism provides a concrete connection between the distributed-system aspects of the original project and the intelligent-system aspects of the new project: the distributed system detects the loss of a participant, while the intelligent system provides an autonomous agent capable of taking over the participant's role.

For the implementation of the agent system, we plan to use **SPADE-BDI**, a Python-based agent framework. This choice allows the intelligent agents to be integrated directly with the existing Python/Pygame implementation without introducing a Java/Python bridge. The game engine will remain responsible for the simulation, world state and execution of low-level actions, while the agent layer will be responsible for perception, beliefs, deliberation and communication.

The resulting system will therefore be used as a concrete case study for investigating **autonomous agents, BDI reasoning, multi-agent coordination and autonomous substitution in a distributed environment**. The project will evaluate whether these techniques provide meaningful advantages over conventional rule-based behaviors, particularly in scenarios involving multiple objectives, partial information, interaction between agents and changes in the availability of human participants.
