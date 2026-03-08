# Windows Server 2025 on Harvester — Deployment Guide

Automated deployment of Windows Server 2025 (Core or Desktop Experience) on a
Harvester HCI cluster, with unattended installation and SUSE VMDP VirtIO drivers.

Works in both **online** and **air-gapped** environments.

---

## Table of Contents

**[1. Quick Start](#quick-start)**

**[2. Architecture](#architecture)**
- [VM disk layout](#vm-disk-layout)
- [Boot sequence](#boot-sequence)

**[3. Prerequisites](#prerequisites)**
- [Software](#software)
- [Harvester setup](#harvester-setup)
- [Air-gapped environment](#air-gapped-environment)

**[4. Deployment](#deployment)**
- [Start the generator](#start-the-generator)
- [Configure and build the ISO](#configure-and-build-the-iso)
- [Serve the ISO and deploy](#serve-the-iso-and-deploy)
- [Monitor and verify](#monitor-and-verify)
- [Terraform variables](#terraform-variables)

**[5. Post-installation](#post-installation)**
- [Access methods](#access-methods)
- [Optional optimizations](#optional-optimizations)
- [Deploy additional VMs](#deploy-additional-vms)

**[6. Reference](#reference)**
- [VirtIO drivers (SUSE VMDP)](#virtio-drivers-suse-vmdp)
- [File structure](#file-structure)

**[7. Troubleshooting](#troubleshooting)**

---

## Quick Start

### 1. Build and start the generator

```bash
# Clone the project
git clone https://github.com/jniedergang/HarvesterAutoMS.git
cd HarvesterAutoMS

# Build the container image (includes Flask, mkisofs, VMDP drivers)
podman build -t localhost/autounattend-generator:latest -f Containerfile .

# Create the container with data volumes
podman create --name autounattend-generator -p 8098:8098 \
  -v ./iso:/app/iso:z -v ./xml:/app/xml:z -v ./configs:/app/configs:z \
  -v ./drivers:/app/drivers:z -v ./images:/app/images:z \
  localhost/autounattend-generator:latest

# Start
podman start autounattend-generator
```

The generator listens on port **8098** (configurable in `app.py`).

### 2. Generate the autounattend ISO

Open `http://<workstation-ip>:8098` in a browser.

- Fill in: **hostname**, **static IP**, **password**, **gateway**, **DNS**, **timezone**
- Verify **vmdp** drivers are checked in the Drivers section
- Click **Build ISO**
- The ISO is generated in the `iso/` directory (~1.6 MB)

### 3. Upload the Windows ISO to Harvester

If not already done, import the Windows Server 2025 installation ISO (~6.2 GB):

```
Harvester UI > Images > Create > Upload File
  Name:   windows-server-2025
  Source: Upload (or URL if accessible)
  --> Note the resulting image ID (e.g. default/image-xxxxx)
```

The ISO is available from the [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2025) or VLSC.

### 4. Serve the autounattend ISO and deploy

```bash
# Serve the ISO locally — Harvester downloads it via HTTP
cd iso/
python3 -m http.server 8199 &

# Deploy the VM with Terraform
cd <terraform-dir>/
terraform init    # first time only
terraform apply \
  -var 'admin_password=YourSecurePassword' \
  -var 'windows_iso_image=default/image-xxxxx' \
  -var 'autounattend_iso_url=http://<workstation-ip>:8199/<iso-filename>.iso'
```

Deployment takes approximately **15–20 minutes**. Monitor progress via the VNC console in Harvester UI.

### 5. Connect

```bash
ssh Administrator@<vm-ip>
```

For more details, see [Prerequisites](#prerequisites) and [Deployment](#deployment).

---

## Architecture

### VM disk layout

The VM boots with **4 disks** attached:

```
  +================================================================+
  |                    Harvester VM (q35 machine)                    |
  |                                                                |
  |  +------------------+  +------------------+  +---------------+ |
  |  | Disk 1           |  | Disk 2           |  | Disk 3        | |
  |  | CD-ROM (sata)    |  | Rootdisk (virtio)|  | CD-ROM (sata) | |
  |  | Windows ISO      |  | 80 Gi            |  | VMDP drivers  | |
  |  | boot_order = 1   |  | boot_order = 2   |  | (container)   | |
  |  | ~6 GB            |  |                  |  | ~70 MB        | |
  |  +------------------+  +------------------+  +---------------+ |
  |                                                                |
  |  +------------------+                                          |
  |  | Disk 4           |     NIC: e1000 (or virtio post-install)  |
  |  | CD-ROM (sata)    |     Network: bridge on mgmt              |
  |  | autounattend ISO |                                          |
  |  | XML + drivers    |                                          |
  |  | ~1.6 MB          |                                          |
  |  +------------------+                                          |
  +================================================================+
```

The SUSE VMDP drivers are embedded in Disk 4 under `$WinPEDriver$/` — a magic
folder that Windows PE auto-scans on all mounted media. This is more reliable
than the `DriverPaths` XML approach, which requires guessing CD-ROM drive letters.

### Boot sequence

```
  First boot                             Subsequent boots
  ~~~~~~~~~~                             ~~~~~~~~~~~~~~~~

  CD-ROM 1 (boot=1)                      Rootdisk (boot=2)
       |                                      |
       v                                      v
  Windows PE starts                      Windows boots normally
       |
       v
  Auto-loads VirtIO drivers
  from $WinPEDriver$/ on Disk 4
       |
       v
  Reads autounattend.xml → unattended install:
  partitioning, Windows copy, hostname, timezone
       |
       v
  Reboot → FirstLogonCommands:
  static IP, DNS, OpenSSH, WinRM, firewall rules
       |
       v
  VM READY — ssh Administrator@<vm-ip>
```

**Autounattend passes:** windowsPE (partitioning, drivers, image selection) → specialize (hostname, timezone, RDP) → oobeSystem (password, auto-logon scripts, then disable).

---

## Prerequisites

### Software

| Component | Purpose | Install |
|-----------|---------|---------|
| Podman | Container runtime (build + VMDP extraction) | `zypper install podman` |
| Terraform >= 1.5 | Infrastructure deployment | [terraform.io](https://www.terraform.io/) |
| Harvester Terraform provider | VM provisioning (`0.0.0-dev`) | Build from source |
| Python 3 + Flask | Autounattend Generator web UI | `zypper install python3 python3-Flask` |
| mkisofs or genisoimage | ISO creation | `zypper install mkisofs` |

### Harvester setup

| Resource | Required state |
|----------|---------------|
| Harvester HCI | Running, accessible via HTTPS |
| VM network | Configured (bridge mode on mgmt cluster network) |
| Storage class | Available (default: `longhorn`) |
| Kubeconfig | Available locally (`rke2.yaml`) |
| Windows Server 2025 ISO | Uploaded as a Harvester image |

Upload the Windows ISO (~6.2 GB) from [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2025) or VLSC:
`Harvester UI > Images > Create > Upload File` — note the resulting image ID.

The deployment workstation must be reachable from Harvester on port 8199
(temporary HTTP server). The target VM IP must be free on the network.

### Air-gapped environment

In an offline environment, pre-stage all dependencies:

| Resource | Preparation |
|----------|-------------|
| Windows Server 2025 ISO | Upload to Harvester as image |
| SUSE VMDP container image | `podman pull` + `podman save` on a connected machine, then `podman load` on the air-gapped workstation |
| Autounattend Generator container | `podman build` from the Containerfile (offline once built) |
| Terraform + Harvester provider | Download binaries, place in PATH / `~/.terraform.d/plugins/` |

**VMDP drivers without Podman** — extract manually on a connected machine and transfer:

```bash
podman create --name vmdp-tmp registry.suse.com/suse/vmdp/vmdp:2.5.4.3
podman cp vmdp-tmp:/disk/VMDP-WIN-2.5.4.3.iso ./
podman rm vmdp-tmp
mkdir -p /tmp/vmdp-mount
sudo mount -o loop VMDP-WIN-2.5.4.3.iso /tmp/vmdp-mount
cp -r /tmp/vmdp-mount/win10-11-server22/x64/pvvx/* drivers/vmdp/
sudo umount /tmp/vmdp-mount
```

Place the extracted files in `drivers/vmdp/` on the air-gapped workstation.
The generator will include them in the ISO without needing any network access.

---

## Deployment

### Start the generator

**Container (recommended):**

```bash
podman build -t localhost/autounattend-generator:latest -f Containerfile .

podman create --name autounattend-generator -p 8098:8098 \
  -v ./iso:/app/iso:z -v ./xml:/app/xml:z -v ./configs:/app/configs:z \
  -v ./drivers:/app/drivers:z -v ./images:/app/images:z \
  localhost/autounattend-generator:latest

podman start autounattend-generator
```

**Local dev mode:** `./start.sh`

Open `http://<workstation-ip>:8098`.

### Configure and build the ISO

1. Fill in **Basic Parameters** (hostname, IP, password, subnet, gateway, DNS, timezone)
2. The **XML Preview** updates in real-time
3. Open **Drivers** — verify `vmdp` is checked (9 .inf files, ~1.2 MB)
4. Click **Build ISO** — the console shows progress and produces an ISO in `iso/`

### Serve the ISO and deploy

```bash
# Serve the ISO via HTTP (Harvester downloads it)
cd iso/
python3 -m http.server 8199 &
HTTP_PID=$!

# Deploy with Terraform
cd <terraform-dir>/
terraform init                    # first time only
terraform apply \
  -var 'admin_password=YourSecurePassword' \
  -var 'autounattend_iso_url=http://<workstation-ip>:8199/<iso-filename>.iso'
```

Or use a `terraform.tfvars` file:

```hcl
admin_password       = "YourSecurePassword"
vm_name              = "win2025-core"
vm_ip                = "10.0.0.50"
vm_subnet            = "255.255.255.0"
network_gateway      = "10.0.0.1"
network_nameservers  = ["10.0.0.2"]
autounattend_iso_url = "http://10.0.0.10:8199/autounattend-win2025-core.iso"
```

### Monitor and verify

Open the **VNC console** in Harvester UI (`Virtual Machines > <vm-name> > Console`):

| Time | Screen |
|------|--------|
| 0–2 min | "Windows is loading files..." (PE boots) |
| 2–5 min | "Getting devices ready..." (VirtIO drivers) |
| 5–15 min | "Installing Windows..." (file copy) |
| 15–18 min | Reboot, "Getting ready..." (OOBE + setup scripts) |
| 18–20 min | Login screen or cmd prompt — **VM ready** |

```bash
kill $HTTP_PID                                    # stop temp HTTP server
ssh Administrator@<vm-ip>                         # test SSH
hostname && ipconfig                              # verify name + IP
Get-Service sshd,WinRM | Format-Table Name,Status # verify services
```

**Post-install cleanup:** remove CD-ROM disks in `Harvester UI > VM > Edit Config > Volumes`
(keep only rootdisk). The `lifecycle { ignore_changes = [disk] }` Terraform block prevents conflicts.

### Terraform variables

| Variable | Default | Description |
|----------|---------|-------------|
| `vm_name` | `win2025-core` | VM name and hostname (max 15 chars) |
| `vm_cpu` | `4` | vCPUs |
| `vm_memory` | `"8Gi"` | RAM |
| `vm_disk_size` | `"80Gi"` | System disk size |
| `vm_ip` | — | Static IP address |
| `vm_subnet` | `"255.255.255.0"` | Subnet mask |
| `network_gateway` | — | Default gateway |
| `network_nameservers` | — | DNS server(s) |
| `network_name` | `"default/production"` | Harvester VM network |
| `admin_password` | *(required)* | Administrator password (sensitive) |
| `windows_iso_image` | — | Harvester image ID for the Windows ISO |
| `vmdp_image` | `"registry.suse.com/suse/vmdp/vmdp:2.5.4.3"` | SUSE VMDP container image |
| `autounattend_iso_url` | — | URL where the autounattend ISO is served |

---

## Post-installation

### Access methods

| Protocol | Endpoint | Notes |
|----------|----------|-------|
| **SSH** | `ssh Administrator@<vm-ip>` | OpenSSH Server, PowerShell default shell |
| **WinRM** | `http://<vm-ip>:5985/wsman` | PowerShell remoting (basic auth) |
| **RDP** | `<vm-ip>:3389` | For RSAT tools (no desktop in Core mode) |

### Optional optimizations

**Switch NIC to VirtIO** — shut down VM, change `model = "e1000"` to `model = "virtio"` in Terraform (or Harvester UI), apply, boot. Windows auto-detects the pre-installed VirtIO drivers.

**Enable ICMP (ping)** — Windows firewall blocks it by default:
```powershell
New-NetFirewallRule -DisplayName "Allow ICMPv4" -Protocol ICMPv4 -IcmpType 8 -Action Allow
```

### Deploy additional VMs

Change parameters in the generator UI, build a new ISO, then either:
- Different variables: `terraform apply -var 'vm_name=win2025-db' -var 'vm_ip=...'`
- Terraform workspaces: `terraform workspace new win2025-db && terraform apply ...`

---

## Reference

### VirtIO drivers (SUSE VMDP)

```
$WinPEDriver$/                    Magic folder — Windows PE auto-scans it

  pvvxblk.inf / .sys / .cat       Block storage (virtio disk)
  pvvxscsi.inf / .sys / .cat      SCSI controller
  pvvxnet.inf / .sys / .cat       Network adapter
  pvvxbn.inf / .sys / .cat        Balloon driver (memory management)
  virtio_serial.inf / .sys        Serial port
  virtio_rng.inf / .sys           Random number generator
  virtio_fs.inf / .sys            Filesystem sharing
  fwcfg.inf / .sys                Firmware config
  pvcrash_notify.inf / .sys       Crash notification
```

The generator manages drivers automatically: built-in VMDP drivers are copied
to `drivers/vmdp/` at first start, the UI selects which sources to include,
and the build script embeds them into `$WinPEDriver$/` in the ISO.

### File structure

```
<generator-source>/                 Generator source (in git)
  app.py                            Flask backend
  index.html                        Frontend web UI
  build-iso-from-xml.sh             ISO creation script
  Containerfile                     Multi-stage container image
  start.sh                          Local dev launcher
  GUIDE.md / GUIDE-fr.md            This document (EN/FR)

<terraform-dir>/                    Terraform + runtime data
  *.tf                              Provider, variables, VM, images, outputs
  rke2.yaml                         Kubeconfig for Harvester
  configs/                          Saved configurations (from UI)
  iso/                              Generated autounattend ISOs
  xml/                              Saved autounattend XMLs
  drivers/{vmdp,custom}/            Driver sources
  images/                           Windows ISOs (served via HTTP)
```

---

## Troubleshooting

### VirtIO disk not detected

**Symptom:** Windows PE shows no disk at "Select location to install".

Verify the ISO contains drivers: `isoinfo -l -i <iso> | grep WinPEDriver`.
Rebuild if missing — ensure `drivers/vmdp/` has the driver files before building.

### Wrong Windows edition

**Symptom:** Installs Desktop Experience instead of Core.

| Index | Name | Type |
|-------|------|------|
| 1 | Windows Server 2025 Standard Evaluation | Core (no GUI) |
| 2 | Windows Server 2025 Standard Evaluation (Desktop Experience) | GUI |
| 3 | Windows Server 2025 Datacenter Evaluation | Core |
| 4 | Windows Server 2025 Datacenter Evaluation (Desktop Experience) | GUI |

The name must match **exactly**. Use the Edition selector in the generator.

### Terraform timeout

**Symptom:** Error after 30 minutes.

Check VNC console — install may be stuck. Increase timeout: `timeouts { create = "45m" }`.

### No network after install

| Cause | Solution |
|-------|----------|
| Windows firewall blocks ICMP | Use SSH instead of ping |
| NIC adapter name mismatch | Check via VNC: `Get-NetAdapter` |
| FirstLogonCommands didn't run | Run manually: `netsh interface ip set address "Ethernet" static <ip> <mask> <gw>` |

### VM reboots into installer

Remove the Windows ISO disk from Harvester UI.

### SSH password rejected

Try: `ssh -o PreferredAuthentications=password Administrator@<vm-ip>`
