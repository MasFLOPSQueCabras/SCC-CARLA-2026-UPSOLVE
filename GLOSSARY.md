# Glossary

## BMC

**Baseboard Management Console**: A specialized, independent service processor embedded on a server's motherboard that provides out-of-band (OOB) hardware management and monitoring. Operating independently of the host CPU, BIOS/UEFI, and operating system, a BMC enables administrators to monitor system telemetry (temperatures, voltages, fan speeds), cycle power (power on, power off, reset), and access the console remotely even when the primary OS is unresponsive or not installed. In SCC@CARLA, each cluster node has an internal BMC IP address under `10.1.<TID>.<NODE>`.

## HPE iLO

**Integrated Lights-Out**: Hewlett Packard Enterprise's proprietary implementation of a BMC embedded in HPE ProLiant servers. HPE iLO features its own processor, RAM, ROM, and dedicated or shared network interface, providing comprehensive remote server management features:
- **Remote Console**: Direct keyboard, video, and mouse (KVM) access over the network via web browser or CLI.
- **Power Control**: Remote power cycling, graceful shutdown, and hard reboots.
- **Virtual Media**: Mounting remote optical disk and storage images (ISO files) over HTTP/HTTPS across the network to boot installation media without physical intervention.
- **RESTful API (`ilorest`)**: Redfish-compliant programmatic interface used for automated provisioning, remote configuration, and BIOS backup/restore.

## Tailscale

A zero-configuration mesh virtual private network (VPN) built on top of the WireGuard protocol. Tailscale assigns stable IP addresses and domain names (via MagicDNS) to each device on a private overlay network ("tailnet") and automatically negotiates encrypted peer-to-peer tunnels across firewalls and NATs. In SCC@CARLA, teams use Tailscale to establish secure connectivity between their local workstations and the shared bastion host (`bastion.tail263e10.ts.net`).

## Wireguard

A modern, high-performance open-source communication protocol and virtual private network (VPN) architecture. Operating directly in the Linux kernel space, WireGuard uses state-of-the-art cryptography (Noise protocol framework, Curve25519, ChaCha20, Poly1305, BLAKE2s) and maintains a minimal code footprint, delivering significantly lower latency and higher throughput compared to legacy protocols like IPsec and OpenVPN. Tailscale uses WireGuard as its underlying cryptographic transport.

## Bastion Host

A dedicated, hardened server positioned as a single, secure gateway between external networks (such as the Internet or Tailscale VPN) and private internal subnets (such as the cluster nodes and BMC management networks). All administrative access to internal resources must traverse the bastion host, isolating the internal cluster infrastructure from direct external access.

## Virtual Media

An out-of-band management capability (provided by iLO) that enables mounting remote storage images (such as an OS `.iso` file) over the network as if the drive were physically attached to the server. In this environment, virtual media is paired with an HTTP server hosted on the bastion machine to perform unattended or manual operating system installations on cluster nodes.

## ilorest

**HPE iLO RESTful Interface Tool**: A command-line utility provided by HPE that interacts with the Redfish-compliant iLO REST APIs. It allows administrators to script and automate BMC tasks from the terminal, such as logging into node BMCs, configuring network settings, setting virtual media URLs for booting, rebooting nodes, and saving/restoring BIOS configuration snapshots (`ilorest save` / `ilorest load`).

## InfiniBand

A high-throughput, extremely low-latency switched fabric communications link predominantly used in high-performance computing (HPC) clusters. InfiniBand provides Remote Direct Memory Access (RDMA) capabilities, offloading protocol processing and memory access from the CPU to maximize node-to-node communication speeds for distributed parallel workloads.

## Subnet Manager (OpenSM)

A centralized control software service in an InfiniBand network responsible for discovering network topology, assigning Local Identifiers (LIDs) to all end ports and switches, computing routing paths, and monitoring link states. In SCC@CARLA, the InfiniBand Subnet Manager is centrally managed by the infrastructure administrators, so teams do not need to configure or run OpenSM themselves.

## SSH Tunneling (Port Forwarding)

A method of routing arbitrary application network traffic through an encrypted SSH connection. For example, local port forwarding (`ssh -L <local_port>:<remote_ip>:<remote_port>`) allows a browser on a local machine to reach internal services (such as the HTTPS iLO web interface on port 443 of `10.1.<TID>.<NODE>`) through the bastion host.

## Out-of-Band (OOB) Management

A system administration practice where server hardware is monitored and managed via an auxiliary, dedicated communication path (such as a separate network interface connected to the BMC) that is physically or logically isolated from the in-band production data network. OOB management ensures system accessibility regardless of the state of the host operating system.