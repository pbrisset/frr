# FRR Development And Topotest Reference Guide

This guide provides a repeatable university lab workflow for building FRRouting (FRR), running topotests, and analyzing EVPN host mobility experiments.

Supported platforms:

- macOS using Colima and the Docker CLI
- PC running Ubuntu 22.04 LTS or Ubuntu 24.04 LTS

All commands are intended for a terminal. Commands marked "inside the container" must be run after entering the FRR development container.

## 1. Platform Setup

Choose the setup that matches your computer.

### 1.1 macOS With Colima

Colima provides the Linux virtual machine needed to run Docker containers on macOS.

Install Homebrew if it is not already installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install Colima, Docker CLI, Git, and Wireshark:

```bash
brew install colima docker git
brew install --cask wireshark
```

Start Colima with enough resources for FRR topotests:

```bash
colima start --cpu 4 --memory 8 --disk 60
```

Verify Docker works:

```bash
docker run hello-world
```

Verify that the Colima Linux VM exposes kernel modules to Docker:

```bash
docker run --rm --privileged -v /lib/modules:/lib/modules ubuntu:22.04 bash -lc 'test -d /lib/modules && ls /lib/modules'
```

Expected result: the command prints at least one kernel module directory. If it prints nothing or reports that `/lib/modules` is missing, stop and restart Colima:

Install the required kernel modules for FRR topotest:

```
# This enters the colima VM
colima ssh
sudo apt-get update
sudo apt-get install linux-modules-extra-$(uname -r)
sudo modprobe vrf && sudo modprobe mpls_router && sudo modprobe team
# Add them to preserve accross reboots
----
cat /etc/modules-load.d/modules.conf 
# /etc/modules is obsolete and has been replaced by /etc/modules-load.d/.
# Please see modules-load.d(5) and modprobe.d(5) for details.
#
# Updating this file still works, but it is undocumented and unsupported.
vrf
mpls_router
team
----
exit
```

Additional commands:

```bash
colima list
colima stop
colima start --cpu 4 --memory 8 --disk 60
```

Useful Colima commands:

```bash
colima status
colima stop
colima start
```

macOS notes:

- Keep the FRR workspace under your home directory, for example `~/src/frr`, so Colima can mount it into containers.
- If a web visualizer uses port `5000`, macOS AirPlay Receiver may already be using that port. Disable AirPlay Receiver or publish a different port.
- Packet captures copied out of the container can be opened with Wireshark on macOS.

### 1.2 PC With Ubuntu

Use Ubuntu 22.04 LTS or Ubuntu 24.04 LTS on the PC. A native install is preferred. An Ubuntu VM can also work if it has enough CPU, RAM, disk, and nested virtualization support.

Recommended resources:

| Resource | Recommended |
| --- | --- |
| RAM | 8 GB or more |
| CPU | 4 cores if available |
| Disk | 40 GB minimum, 60 GB preferred |

Update Ubuntu:

```bash
sudo apt update
sudo apt upgrade -y
```

Install required packages:

```bash
sudo DEBIAN_FRONTEND=noninteractive apt install -y git docker.io docker-buildx build-essential tmux python3-pytest python3-pip tcpdump tshark wireshark
```

Enable Docker:

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

Allow your user to run Docker:

```bash
sudo usermod -aG docker "$USER"
```

Reboot so the group change applies:

```bash
reboot
```

After reboot, verify Docker works:

```bash
docker run hello-world
```

If Docker reports a permission error after reboot, check group membership:

```bash
groups
```

The output should include `docker`.

## 2. Clone FRR

This Capstone lab uses the `pbrisset/frr` fork and its `capstone` branch.
Run these commands on macOS or Ubuntu:

```bash
mkdir -p ~/src
cd ~/src
git clone --branch capstone https://github.com/pbrisset/frr.git
cd frr
```

Confirm you are on the `capstone` branch:

```bash
git branch --show-current
```

Expected result: the command prints `capstone`.

For development work, create your own branch off `capstone`:

```bash
git checkout -b dev/<userid>.<short-description>
```

Example:

```bash
git checkout -b dev/student01.evpn_mobility
```

## 3. Build The FRR Development Image

Run from the FRR repository root:

```bash
cd ~/src/frr
```

Use Ubuntu 22.04 for the container image unless the course staff gives a different version:

```bash
docker build -t frr-dev:latest --build-arg=UBUNTU_VERSION=22.04 --build-arg=UID="$(id -u)" -f docker/ubuntu-ci/Dockerfile .
```

If your assigned FRR branch requires Ubuntu 20.04:

```bash
docker build -t frr-dev:latest --build-arg=UBUNTU_VERSION=20.04 --build-arg=UID="$(id -u)" -f docker/ubuntu-ci/Dockerfile .
```

Check that the image exists:

```bash
docker images | grep frr-dev
```

## 4. Create And Enter The FRR Development Container

Run from the FRR repository root:

```bash
cd ~/src/frr
```

Create the container:

```bash
docker run --init -it --privileged --name frr-dev -v /lib/modules:/lib/modules -v "$(pwd)":/home/frr/frr frr-dev:latest bash
```

If the visualizer will be used later, create the container with port `5000` published:

```bash
docker run --init -it --privileged -p 5000:5000 --name frr-dev -v /lib/modules:/lib/modules -v "$(pwd)":/home/frr/frr frr-dev:latest bash
```

Important: Docker ports are published when the container is created. If the container was created without `-p 5000:5000`, remove and recreate it before using the visualizer.

Expected result: the terminal is now inside the FRR development container.

If you exit the shell, the container stops. Start it again and open a fresh shell with:

```bash
docker start frr-dev
docker exec -it frr-dev bash
```

## 5. Build FRR Inside The Container

Run inside the container:

```bash
cd ~/frr
./bootstrap.sh
./configure \
  --prefix=/usr \
  --localstatedir=/var \
  --sbindir=/usr/lib/frr \
  --sysconfdir=/etc \
  --enable-vtysh \
  --enable-pimd \
  --enable-sharpd \
  --enable-multipath=256 \
  --enable-user=frr \
  --enable-group=frr \
  --enable-vty-group=frrvty \
  --enable-snmp=agentx \
  --enable-scripting \
  --with-pkg-extra-version=-my-manual-build
make -j"$(nproc)"
sudo make install
```

For debugging with `gdb`, add this option to the `./configure` command:

```bash
--enable-dev-build
```

Check the install:

```bash
vtysh --version
```

## 6. Container Management

Run these commands on the host terminal, not inside the container.

List running containers:

```bash
docker ps
```

List all containers:

```bash
docker ps -a
```

Start a stopped container:

```bash
docker start frr-dev
```

Attach to that container:

```bash
docker attach frr-dev
```

Open a new shell in a running container:

```bash
docker exec -it frr-dev bash
```

For normal lab work, use `docker exec -it frr-dev bash` each time you need a shell. This also allows multiple shells at the same time, which is useful when running the visualizer in one shell and tests in another.

Stop the container:

```bash
docker stop frr-dev
```

Remove the container:

```bash
docker rm -f frr-dev
```

Remove the image:

```bash
docker rmi frr-dev:latest
```

If Docker says the container name already exists, remove the old container or choose a new name.

## 7. Rebuild After Code Changes

Run inside the container:

```bash
cd ~/frr
make -j"$(nproc)"
sudo make install
```

Run unit tests:

```bash
make check
```

Rebuild the Docker image only when the Dockerfile or container dependencies change:

```bash
cd ~/src/frr
docker build -t frr-dev:latest --build-arg=UBUNTU_VERSION=22.04 --build-arg=UID="$(id -u)" -f docker/ubuntu-ci/Dockerfile .
```

## 8. Run FRR Topotests

Topotests must run inside the FRR development container.

Start and attach to the container:

```bash
cd ~/src/frr
docker start frr-dev
docker exec -it frr-dev bash
```

Start `tmux` for long-running or interactive tests:
Check the commands on https://tmuxcheatsheet.com/

```bash
tmux
```

Run one test directory:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest -s <test-directory>
```

Run one test file:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest -s <test-directory>/<test-file>.py
```

Run all topotests:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest -n"$(nproc)" --dist=loadfile
```

The full topotest suite can take a long time and may require more resources than a small laptop or VM provides.

## 9. Useful Topotest Options

Build the topology without running assertions:

```bash
sudo -E pytest --topology-only <test-directory>/<test-file>.py
```

Capture packets on all supported interfaces:

```bash
sudo -E pytest --pcap=all <test-directory>/<test-file>.py
```

Pause and open a VTYSH session for a router:

```bash
tmux
cd /home/frr/frr/tests/topotests
sudo -E pytest -s --pause --vtysh=<router-name> <test-directory>
```

Pause on failure and open VTYSH:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest --vtysh-on-error <test-directory>
```

Detach from tmux:

```text
Ctrl+b, then d
```

Reattach to tmux:

```bash
tmux attach
```

## 10. Install Missing Packages Inside The Container

Install `tcpdump` if packet capture commands fail:

```bash
sudo -E apt-get update
sudo -E apt-get install -y tcpdump
```

Install visualizer dependencies if needed:

```bash
python3 -m pip install --user flask flask-socketio requests
```

Use this pattern for other packages:

```bash
sudo -E apt-get install -y <package-name>
```

## 11. Copy Packet Captures Out Of The Container

Topotest results are normally written under `/tmp/topotests` inside the container.

Create a capture folder on the host:

```bash
mkdir -p ~/mycaptures
```

Copy all topotest results out of the container:

```bash
docker cp frr-dev:/tmp/topotests ~/mycaptures/
```

Copy one specific capture:

```bash
docker cp frr-dev:/tmp/topotests/<test-run>/<node>/<file>.pcap ~/mycaptures/
```

Open `.pcap` files with Wireshark.


## 12. Visualizer Example

The visualizer is a Flask server that runs inside the FRR container and is viewed from the host browser.

The container must be created with port `5000` published:

```bash
docker rm -f frr-dev
docker run --init -it --privileged -p 5000:5000 --name frr-dev -v /lib/modules:/lib/modules -v "$(pwd)":/home/frr/frr frr-dev:latest bash
```

Inside the container:

```bash
python3 -m pip install --user flask flask-socketio requests
tmux new -s server
cd ~/frr/tests/topotests/bgp_evpn_capstone
python3 visualizer_server.py
```

Open on the host:

```text
http://localhost:5000
```

Detach from the server session:

```text
Ctrl+b, then d
```

The page shows only the legend until a test publishes the topology. Open a second container shell from the host:

```bash
docker exec -it frr-dev bash
```

Run the test from the second shell:

```bash
cd ~/frr/tests/topotests
sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py
```

Once the routers start, the test sends the spines, VTEPs, hosts, and links to the visualizer. The server keeps these events, so you can open or refresh the page at any time during the run. If the server is not running, the test logs a message and continues.

The test sends events to `http://127.0.0.1:5000` by default. To use a different server, set `VISUALIZER_URL`:

```bash
VISUALIZER_URL=http://127.0.0.1:5001 sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py
```

## 13. Live BGP Route Chart

The route chart plots BGP EVPN route counts per leaf while the test runs. Every sampling interval, the test runs these commands on each leaf (`leaf1` to `leaf5`):

| Chart metric | Command | Value used |
| --- | --- | --- |
| RT-2 MAC/IP routes | `show bgp l2vpn evpn route type macip json` | `numPrefix` |
| All EVPN routes | `show bgp l2vpn evpn route json` | `numPrefix` |
| Prefixes received | `show bgp l2vpn evpn summary json` | Sum of `pfxRcd` over all peers |

Use the buttons above the chart to switch metrics. The chart draws one line per leaf plus a dashed `total` line. The boxes below the chart show the latest fabric-wide total for each metric.

During host mobility, watch the RT-2 metric. When a host moves, the old leaf withdraws its MAC/IP route and the new leaf advertises it, so each leaf's line shows how it converges.

Start the visualizer server first:

```bash
cd ~/frr
python3 tests/topotests/bgp_evpn_capstone/visualizer_server.py
```

Run the test:

```bash
cd ~/frr/tests/topotests
sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py
```

Open on the host:

```text
http://localhost:5000/route-chart
```

Optional settings:

| Variable | Meaning |
| --- | --- |
| `ENABLE_LIVE_ROUTE_GRAPH=true\|false` | Enable or disable route sampling; default is `true` |
| `ROUTE_SAMPLE_INTERVAL_SECONDS=<float>` | Sampling interval; default is `1.0`, minimum is `0.5` |
| `VISUALIZER_URL=<url>` | Visualizer server address; default is `http://127.0.0.1:5000` |

Example:

```bash
ROUTE_SAMPLE_INTERVAL_SECONDS=0.5 sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py
```

## 14. Troubleshooting

### Docker permission denied on Ubuntu

Add the user to the Docker group and reboot:

```bash
sudo usermod -aG docker "$USER"
reboot
```

### Docker cannot connect on macOS

Confirm Colima is running:

```bash
colima status
```

Start it if needed:

```bash
colima start --cpu 4 --memory 8 --disk 60
```

### Container name already exists

```bash
docker ps -a
docker rm -f frr-dev
```

### Container is stopped

```bash
docker start frr-dev
docker exec -it frr-dev bash
```

### Image cannot be removed

A container still depends on the image. Remove the container first:

```bash
docker ps -a
docker rm <container-name-or-id>
docker rmi frr-dev:latest
```

### Topotest interactive commands fail

Run the test inside `tmux`:

```bash
tmux
```

### Packet captures are missing

Install `tcpdump` inside the container:

```bash
sudo -E apt-get update
sudo -E apt-get install -y tcpdump
```

### Visualizer page does not load

Check that the container was created with `-p 5000:5000`:

```bash
docker ps
```

If the port is missing, remove and recreate the container with the port published.

On macOS, also check whether AirPlay Receiver is using port `5000`.

### Visualizer shows only the legend

The graph is empty until a test publishes the topology. Check these points:

- `visualizer_server.py` is running before or during the test.
- The test log has no `Visualizer not reachable` message.
- The host browser has internet access. The page loads `socket.io` and `vis-network` from public CDNs.

### Colima kernel module check fails

Restart Colima and rerun the check:

```bash
colima stop
colima start --cpu 4 --memory 8 --disk 60
docker run --rm --privileged -v /lib/modules:/lib/modules ubuntu:22.04 bash -lc 'test -d /lib/modules && ls /lib/modules'
```

If the check still fails, use the Ubuntu PC workflow or an Ubuntu VM for topotests that require loadable kernel modules.

## 15. Quick Command Reference

macOS setup:

```bash
brew install colima docker git
brew install --cask wireshark
colima start --cpu 4 --memory 8 --disk 60
docker run hello-world
```

Ubuntu setup:

```bash
sudo apt update
sudo apt upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt install -y git docker.io docker-buildx build-essential tmux python3-pytest python3-pip tcpdump tshark wireshark
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker "$USER"
reboot
```

FRR clone and image build:

```bash
mkdir -p ~/src
cd ~/src
git clone --branch capstone https://github.com/pbrisset/frr.git
cd frr
docker build -t frr-dev:latest --build-arg=UBUNTU_VERSION=22.04 --build-arg=UID="$(id -u)" -f docker/ubuntu-ci/Dockerfile .
```

Container creation:

```bash
docker run --init -it --privileged --name frr-dev -v /lib/modules:/lib/modules -v "$(pwd)":/home/frr/frr frr-dev:latest bash
```

FRR build inside container:

```bash
cd ~/frr
./bootstrap.sh
./configure --prefix=/usr --localstatedir=/var --sbindir=/usr/lib/frr --sysconfdir=/etc --enable-vtysh --enable-pimd --enable-sharpd --enable-multipath=256 --enable-user=frr --enable-group=frr --enable-vty-group=frrvty --enable-snmp=agentx --enable-scripting --with-pkg-extra-version=-my-manual-build
make -j"$(nproc)"
sudo make install
```

Run a topotest:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest -s <test-directory>/<test-file>.py
```

Copy results:

```bash
mkdir -p ~/mycaptures
docker cp frr-dev:/tmp/topotests ~/mycaptures/
```

## 16. Reference Links

- Capstone fork and branch: https://github.com/pbrisset/frr/tree/capstone
- FRRouting upstream repository: https://github.com/FRRouting/frr
- FRR Docker developer documentation: https://docs.frrouting.org/projects/dev-guide/en/latest/building-docker.html
- FRR topotests documentation: https://docs.frrouting.org/projects/dev-guide/en/latest/topotests.html
- Colima documentation: https://colima.run/
- Wireshark: https://www.wireshark.org/
