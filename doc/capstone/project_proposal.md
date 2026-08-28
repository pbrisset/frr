**Title: Warehouse Robot Mobility Using SONiC and FRRouting**

**Description**

Modern warehouses rely on mobile robots to move goods, support inventory
operations, and execute tasks assigned by a central warehouse
controller. These robots must maintain reliable network connectivity
while moving between wireless access points connected to an IP fabric.

This capstone project focuses on emulating robot mobility in a warehouse
network using **SONiC** and **FRRouting**. Students will study how
current networking technologies handle frequent host movement and will
evaluate whether existing mechanisms are sufficient for large-scale
warehouse automation.

Example warehouse scale:

- A small warehouse may have about 10 access points serving up to 100
  robots.

- A large warehouse may scale to 5,000 access points and 50,000 robots.

- Robots may move between access points several times per minute,
  creating frequent network mobility events.

In this project:

- Robots will be modeled as IP hosts.

- The warehouse network will be modeled as an IP fabric.

- The underlay will use eBGP.

- The overlay control plane will use VXLAN-EVPN.

- FRRouting will be used to build, configure, and evaluate the routing
  environment.

Relevant references:

- [FRRouting](https://frrouting.org/)

- [FRRouting Developer
  Guide](https://docs.frrouting.org/projects/dev-guide/en/latest/index.html)

- [RFC 7432: BGP MPLS-Based Ethernet
  VPN](https://www.rfc-editor.org/rfc/rfc7432.html#page-45)

- [RFC 9721](https://datatracker.ietf.org/doc/rfc9721/)

**Project Objectives**

The project has two main objectives.

First, students will characterize existing mobility mechanisms. For
example, EVPN includes a built-in MAC mobility mechanism, but it was not
designed specifically for warehouse-scale robot mobility with very
frequent movement events.

Second, each team will choose one of the following project directions:

1.  **Mobility improvement path**  
    Develop and evaluate new techniques to improve mobility performance.
    Evaluation should consider warehouse size, robot movement rate,
    communication reliability, convergence time, and BGP route churn.

2.  **Monitoring and observability path**  
    Develop a monitoring environment that measures and visualizes robot
    mobility behavior under different warehouse network conditions.

**Project Phases**

**Phase 1: Personal Environment Setup**

Students will prepare their development and simulation environment.

- On macOS: install and configure Colima.

- On Windows or Linux PC: install and configure Ubuntu.

- Install the required tools for building and running FRRouting-based
  simulations.

**Phase 2: FRRouting Familiarization**

Students will learn how to build and operate FRRouting.

Tasks include:

- Clone the FRRouting Git repository.

- Compile the FRRouting code.

- Create simple network topologies.

- Configure routers using FRRouting.

- Bring up a simulation environment.

- Model robot mobility events using FRRouting.

**Phase 3: Mobility Metrics Definition**

Students will identify the metrics needed to characterize robot
mobility.

Examples include:

- Mobility event rate.

- Route update rate.

- BGP convergence time.

- Control-plane load.

- Route-table size.

- Time required for a robot to regain reachability after moving.

**Phase 4: Characterization and Analysis**

Students will run experiments and analyze how the network behaves as
mobility increases.

The analysis should consider:

- Number of robots.

- Number of access points.

- Frequency of robot movement.

- Impact on BGP and EVPN control-plane behavior.

- Scalability limits of the tested design.

**Phase 5: Project Option Implementation**

Each team will implement one of the following options:

1.  Develop strategies to improve robot mobility performance, scale, and
    convergence.

2.  Develop a monitoring environment that demonstrates robot mobility
    performance under different network conditions.

If multiple teams select this project, each team will work on a
different improvement strategy or monitoring approach.

**Skills Required**

Students should have:

- Strong programming skills in C and Python.

- Familiarity with virtual environments, containers, and development
  tooling.

- Basic knowledge of Linux networking.

- Knowledge of BGP and EVPN, or willingness to learn them.

**Student Expectations**

Students are expected to be motivated, curious, and resourceful. This is
a hands-on engineering project involving real networking software and
realistic warehouse automation requirements.

By the end of the project, students should be able to:

- Build and operate an FRRouting-based network simulation.

- Model robot mobility in an IP fabric.

- Measure the impact of mobility on routing behavior.

- Evaluate the scalability of EVPN-based mobility.

- Propose and demonstrate either an improvement strategy or a monitoring
  solution.

This project is suitable for students interested in networking, robotics
infrastructure, distributed systems, Linux, and large-scale automation.
