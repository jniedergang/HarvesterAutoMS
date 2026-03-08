# Windows Server 2025 sur Harvester — Guide de deploiement

Deploiement automatise de Windows Server 2025 (Core ou Desktop Experience) sur
un cluster Harvester HCI, avec installation unattended et drivers VirtIO SUSE VMDP.

Fonctionne en environnement **connecte** et **air-gap** (hors ligne).

---

## Table des matieres

**[1. Demarrage rapide](#demarrage-rapide)**

**[2. Architecture](#architecture)**
- [Disques de la VM](#disques-de-la-vm)
- [Sequence de boot](#sequence-de-boot)

**[3. Prerequis](#prerequis)**
- [Logiciels](#logiciels)
- [Configuration Harvester](#configuration-harvester)
- [Environnement air-gap](#environnement-air-gap)

**[4. Deploiement](#deploiement)**
- [Demarrer le generateur](#demarrer-le-generateur)
- [Configurer et generer l'ISO](#configurer-et-generer-liso)
- [Servir l'ISO et deployer](#servir-liso-et-deployer)
- [Surveiller et verifier](#surveiller-et-verifier)
- [Variables Terraform](#variables-terraform)

**[5. Post-installation](#post-installation)**
- [Methodes d'acces](#methodes-dacces)
- [Optimisations optionnelles](#optimisations-optionnelles)
- [Deployer d'autres VMs](#deployer-dautres-vms)

**[6. Reference](#reference)**
- [Drivers VirtIO (SUSE VMDP)](#drivers-virtio-suse-vmdp)
- [Arborescence des fichiers](#arborescence-des-fichiers)

**[7. Depannage](#depannage)**

---

## Demarrage rapide

Pour les utilisateurs presses — 5 commandes pour deployer une VM Windows :

```bash
# 1. Demarrer le generateur (si pas deja lance)
podman start autounattend-generator    # ou: ./start.sh

# 2. Ouvrir http://<ip-poste>:8098
#    → Remplir hostname, IP, password, DNS
#    → Cliquer "Build ISO"

# 3. Servir l'ISO
cd iso/ && python3 -m http.server 8199 &

# 4. Deployer
cd <repertoire-terraform>/
terraform apply \
  -var 'admin_password=MotDePasse' \
  -var 'autounattend_iso_url=http://<ip-poste>:8199/<nom-iso>.iso'

# 5. Se connecter (~20 min apres)
ssh Administrator@<ip-vm>
```

Pour la premiere utilisation, voir [Prerequis](#prerequis) pour la configuration initiale.

---

## Architecture

### Disques de la VM

La VM demarre avec **4 disques** attaches :

```
  +================================================================+
  |                    VM Harvester (machine q35)                    |
  |                                                                |
  |  +------------------+  +------------------+  +---------------+ |
  |  | Disque 1         |  | Disque 2         |  | Disque 3      | |
  |  | CD-ROM (sata)    |  | Rootdisk (virtio)|  | CD-ROM (sata) | |
  |  | ISO Windows      |  | 80 Gi            |  | Drivers VMDP  | |
  |  | boot_order = 1   |  | boot_order = 2   |  | (conteneur)   | |
  |  | ~6 Go            |  |                  |  | ~70 Mo        | |
  |  +------------------+  +------------------+  +---------------+ |
  |                                                                |
  |  +------------------+                                          |
  |  | Disque 4         |     NIC : e1000 (ou virtio post-install) |
  |  | CD-ROM (sata)    |     Reseau : bridge sur mgmt             |
  |  | ISO autounattend |                                          |
  |  | XML + drivers    |                                          |
  |  | ~1.6 Mo          |                                          |
  |  +------------------+                                          |
  +================================================================+
```

Les drivers SUSE VMDP sont integres dans le Disque 4 sous `$WinPEDriver$/` — un
dossier magique que Windows PE scanne automatiquement sur tous les medias montes.
C'est plus fiable que l'approche `DriverPaths` dans le XML, qui necessite de
deviner la lettre du lecteur CD-ROM.

### Sequence de boot

```
  Premier demarrage                      Demarrages suivants
  ~~~~~~~~~~~~~~~~~                      ~~~~~~~~~~~~~~~~~~~

  CD-ROM 1 (boot=1)                      Rootdisk (boot=2)
       |                                      |
       v                                      v
  Windows PE demarre                     Windows demarre normalement
       |
       v
  Charge les drivers VirtIO
  depuis $WinPEDriver$/ sur le Disque 4
       |
       v
  Lit autounattend.xml → installation automatique :
  partitionnement, copie Windows, hostname, timezone
       |
       v
  Redemarrage → FirstLogonCommands :
  IP statique, DNS, OpenSSH, WinRM, regles firewall
       |
       v
  VM PRETE — ssh Administrator@<ip-vm>
```

**Passes autounattend :** windowsPE (partitionnement, drivers, selection image) → specialize (hostname, timezone, RDP) → oobeSystem (mot de passe, scripts auto-logon, puis desactivation).

---

## Prerequis

### Logiciels

| Composant | Fonction | Installation |
|-----------|----------|--------------|
| Podman | Runtime conteneur (build + extraction VMDP) | `zypper install podman` |
| Terraform >= 1.5 | Deploiement d'infrastructure | [terraform.io](https://www.terraform.io/) |
| Provider Terraform Harvester | Provisionnement VM (`0.0.0-dev`) | Build depuis les sources |
| Python 3 + Flask | Interface web du generateur | `zypper install python3 python3-Flask` |
| mkisofs ou genisoimage | Creation d'ISO | `zypper install mkisofs` |

### Configuration Harvester

| Ressource | Etat requis |
|-----------|-------------|
| Harvester HCI | En fonctionnement, accessible via HTTPS |
| Reseau VM | Configure (mode bridge sur le cluster network mgmt) |
| Storage class | Disponible (defaut : `longhorn`) |
| Kubeconfig | Disponible localement (`rke2.yaml`) |
| ISO Windows Server 2025 | Uploadee en tant qu'image Harvester |

Uploader l'ISO Windows (~6.2 Go) depuis le [Centre d'evaluation Microsoft](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2025) ou VLSC :
`Harvester UI > Images > Create > Upload File` — noter l'ID image resultant.

Le poste de deploiement doit etre accessible depuis Harvester sur le port 8199
(serveur HTTP temporaire). L'IP cible de la VM doit etre libre sur le reseau.

### Environnement air-gap

Dans un environnement hors ligne, pre-preparer toutes les dependances :

| Ressource | Preparation |
|-----------|-------------|
| ISO Windows Server 2025 | Uploader dans Harvester comme image |
| Image conteneur SUSE VMDP | `podman pull` + `podman save` sur une machine connectee, puis `podman load` sur le poste air-gap |
| Conteneur du generateur | `podman build` depuis le Containerfile (hors ligne une fois construit) |
| Terraform + provider Harvester | Telecharger les binaires, placer dans PATH / `~/.terraform.d/plugins/` |

**Drivers VMDP sans Podman** — extraire manuellement sur une machine connectee et transferer :

```bash
podman create --name vmdp-tmp registry.suse.com/suse/vmdp/vmdp:2.5.4.3
podman cp vmdp-tmp:/disk/VMDP-WIN-2.5.4.3.iso ./
podman rm vmdp-tmp
mkdir -p /tmp/vmdp-mount
sudo mount -o loop VMDP-WIN-2.5.4.3.iso /tmp/vmdp-mount
cp -r /tmp/vmdp-mount/win10-11-server22/x64/pvvx/* drivers/vmdp/
sudo umount /tmp/vmdp-mount
```

Placer les fichiers extraits dans `drivers/vmdp/` sur le poste air-gap.
Le generateur les inclura dans l'ISO sans aucun acces reseau.

---

## Deploiement

### Demarrer le generateur

**Conteneur (recommande) :**

```bash
podman build -t localhost/autounattend-generator:latest -f Containerfile .

podman create --name autounattend-generator -p 8098:8098 \
  -v ./iso:/app/iso:z -v ./xml:/app/xml:z -v ./configs:/app/configs:z \
  -v ./drivers:/app/drivers:z -v ./images:/app/images:z \
  localhost/autounattend-generator:latest

podman start autounattend-generator
```

**Mode dev local :** `./start.sh`

Ouvrir `http://<ip-poste>:8098`.

### Configurer et generer l'ISO

1. Remplir les **Basic Parameters** (hostname, IP, mot de passe, sous-reseau, passerelle, DNS, timezone)
2. L'**apercu XML** se met a jour en temps reel
3. Ouvrir **Drivers** — verifier que `vmdp` est coche (9 fichiers .inf, ~1.2 Mo)
4. Cliquer **Build ISO** — la console affiche la progression et produit une ISO dans `iso/`

### Servir l'ISO et deployer

```bash
# Servir l'ISO via HTTP (Harvester la telecharge)
cd iso/
python3 -m http.server 8199 &
HTTP_PID=$!

# Deployer avec Terraform
cd <repertoire-terraform>/
terraform init                    # premiere fois uniquement
terraform apply \
  -var 'admin_password=MotDePasseSecurise' \
  -var 'autounattend_iso_url=http://<ip-poste>:8199/<nom-iso>.iso'
```

Ou utiliser un fichier `terraform.tfvars` :

```hcl
admin_password       = "MotDePasseSecurise"
vm_name              = "win2025-core"
vm_ip                = "10.0.0.50"
vm_subnet            = "255.255.255.0"
network_gateway      = "10.0.0.1"
network_nameservers  = ["10.0.0.2"]
autounattend_iso_url = "http://10.0.0.10:8199/autounattend-win2025-core.iso"
```

### Surveiller et verifier

Ouvrir la **console VNC** dans Harvester (`Virtual Machines > <nom-vm> > Console`) :

| Temps | Ecran |
|-------|-------|
| 0–2 min | "Windows is loading files..." (PE demarre) |
| 2–5 min | "Getting devices ready..." (drivers VirtIO) |
| 5–15 min | "Installing Windows..." (copie des fichiers) |
| 15–18 min | Redemarrage, "Getting ready..." (OOBE + scripts) |
| 18–20 min | Ecran de login ou invite cmd — **VM prete** |

```bash
kill $HTTP_PID                                    # arreter le serveur HTTP
ssh Administrator@<ip-vm>                         # tester SSH
hostname && ipconfig                              # verifier nom + IP
Get-Service sshd,WinRM | Format-Table Name,Status # verifier les services
```

**Nettoyage :** supprimer les CD-ROMs dans `Harvester UI > VM > Edit Config > Volumes`
(garder uniquement rootdisk). Le bloc `lifecycle { ignore_changes = [disk] }` Terraform evite les conflits.

### Variables Terraform

| Variable | Defaut | Description |
|----------|--------|-------------|
| `vm_name` | `win2025-core` | Nom de la VM et hostname (max 15 car.) |
| `vm_cpu` | `4` | vCPUs |
| `vm_memory` | `"8Gi"` | RAM |
| `vm_disk_size` | `"80Gi"` | Taille du disque systeme |
| `vm_ip` | — | Adresse IP statique |
| `vm_subnet` | `"255.255.255.0"` | Masque de sous-reseau |
| `network_gateway` | — | Passerelle par defaut |
| `network_nameservers` | — | Serveur(s) DNS |
| `network_name` | `"default/production"` | Reseau VM Harvester |
| `admin_password` | *(obligatoire)* | Mot de passe Administrator (sensible) |
| `windows_iso_image` | — | ID image Harvester pour l'ISO Windows |
| `vmdp_image` | `"registry.suse.com/suse/vmdp/vmdp:2.5.4.3"` | Image conteneur SUSE VMDP |
| `autounattend_iso_url` | — | URL ou l'ISO autounattend est servie |

---

## Post-installation

### Methodes d'acces

| Protocole | Endpoint | Notes |
|-----------|----------|-------|
| **SSH** | `ssh Administrator@<ip-vm>` | OpenSSH Server, PowerShell par defaut |
| **WinRM** | `http://<ip-vm>:5985/wsman` | PowerShell remoting (auth basique) |
| **RDP** | `<ip-vm>:3389` | Pour les outils RSAT (pas de bureau en mode Core) |

### Optimisations optionnelles

**Passer la carte reseau en VirtIO** — eteindre la VM, changer `model = "e1000"` en `model = "virtio"` dans Terraform (ou l'UI Harvester), appliquer, demarrer. Windows detecte automatiquement les drivers VirtIO pre-installes.

**Activer ICMP (ping)** — le pare-feu Windows le bloque par defaut :
```powershell
New-NetFirewallRule -DisplayName "Allow ICMPv4" -Protocol ICMPv4 -IcmpType 8 -Action Allow
```

### Deployer d'autres VMs

Modifier les parametres dans l'UI du generateur, construire une nouvelle ISO, puis :
- Variables differentes : `terraform apply -var 'vm_name=win2025-db' -var 'vm_ip=...'`
- Workspaces Terraform : `terraform workspace new win2025-db && terraform apply ...`

---

## Reference

### Drivers VirtIO (SUSE VMDP)

```
$WinPEDriver$/                    Dossier magique — Windows PE le scanne automatiquement

  pvvxblk.inf / .sys / .cat       Stockage bloc (disque virtio)
  pvvxscsi.inf / .sys / .cat      Controleur SCSI
  pvvxnet.inf / .sys / .cat       Carte reseau
  pvvxbn.inf / .sys / .cat        Balloon driver (gestion memoire)
  virtio_serial.inf / .sys        Port serie
  virtio_rng.inf / .sys           Generateur aleatoire
  virtio_fs.inf / .sys            Partage de fichiers
  fwcfg.inf / .sys                Configuration firmware
  pvcrash_notify.inf / .sys       Notification de crash
```

Le generateur gere les drivers automatiquement : les drivers VMDP integres sont
copies dans `drivers/vmdp/` au premier demarrage, l'UI selectionne les sources
a inclure, et le script de build les integre dans `$WinPEDriver$/` dans l'ISO.

### Arborescence des fichiers

```
<source-generateur>/                Sources du generateur (dans git)
  app.py                            Backend Flask
  index.html                        Interface web
  build-iso-from-xml.sh             Script de creation ISO
  Containerfile                     Image conteneur multi-stage
  start.sh                          Lanceur dev local
  GUIDE.md / GUIDE-fr.md            Ce document (EN/FR)

<repertoire-terraform>/             Terraform + donnees runtime
  *.tf                              Provider, variables, VM, images, outputs
  rke2.yaml                         Kubeconfig Harvester
  configs/                          Configurations sauvegardees (depuis l'UI)
  iso/                              ISOs autounattend generees
  xml/                              XMLs autounattend sauvegardes
  drivers/{vmdp,custom}/            Sources de drivers
  images/                           ISOs Windows (servies via HTTP)
```

---

## Depannage

### Disque VirtIO non detecte

**Symptome :** Windows PE ne montre aucun disque a "Select location to install".

Verifier que l'ISO contient les drivers : `isoinfo -l -i <iso> | grep WinPEDriver`.
Reconstruire si absent — verifier que `drivers/vmdp/` contient les fichiers avant le build.

### Mauvaise edition Windows

**Symptome :** Installe Desktop Experience au lieu de Core.

| Index | Nom | Type |
|-------|-----|------|
| 1 | Windows Server 2025 Standard Evaluation | Core (sans GUI) |
| 2 | Windows Server 2025 Standard Evaluation (Desktop Experience) | GUI |
| 3 | Windows Server 2025 Datacenter Evaluation | Core |
| 4 | Windows Server 2025 Datacenter Evaluation (Desktop Experience) | GUI |

Le nom doit correspondre **exactement**. Utiliser le selecteur d'edition du generateur.

### Timeout Terraform

**Symptome :** Erreur apres 30 minutes.

Verifier la console VNC — l'installation est peut-etre bloquee. Augmenter le timeout : `timeouts { create = "45m" }`.

### Pas de reseau apres installation

| Cause | Solution |
|-------|----------|
| Le pare-feu Windows bloque ICMP | Utiliser SSH au lieu de ping |
| Nom de l'adaptateur reseau different | Verifier via VNC : `Get-NetAdapter` |
| FirstLogonCommands n'ont pas tourne | Lancer manuellement : `netsh interface ip set address "Ethernet" static <ip> <masque> <passerelle>` |

### La VM reboote sur l'installeur

Supprimer le disque ISO Windows depuis l'UI Harvester.

### Mot de passe SSH rejete

Essayer : `ssh -o PreferredAuthentications=password Administrator@<ip-vm>`
