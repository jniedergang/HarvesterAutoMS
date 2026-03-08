#!/usr/bin/env python3
"""Windows Autounattend Generator — Flask backend."""

import json
import os
import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context

app = Flask(__name__, static_folder='.', static_url_path='')

CONFIGS_DIR = Path(os.environ.get('CONFIGS_DIR', '/app/configs'))
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR = Path(os.environ.get('OUTPUT_DIR', './iso'))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
XML_DIR = Path(os.environ.get('XML_DIR', './xml'))
XML_DIR.mkdir(parents=True, exist_ok=True)
DRIVERS_DIR = Path(os.environ.get('DRIVERS_DIR', '/app/drivers'))
DRIVERS_DIR.mkdir(parents=True, exist_ok=True)
BUILTIN_DRIVERS = Path('/app/vmdp-drivers-builtin')
IMAGES_DIR = Path(os.environ.get('IMAGES_DIR', '/app/images'))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

build_lock = threading.Lock()


def init_drivers():
    """Copy builtin VMDP drivers to DRIVERS_DIR/vmdp/ if not already present."""
    vmdp_dest = DRIVERS_DIR / 'vmdp'
    if BUILTIN_DRIVERS.is_dir() and any(BUILTIN_DRIVERS.iterdir()):
        if not vmdp_dest.exists() or not any(vmdp_dest.iterdir()):
            vmdp_dest.mkdir(parents=True, exist_ok=True)
            for f in BUILTIN_DRIVERS.iterdir():
                dest = vmdp_dest / f.name
                if f.is_file():
                    shutil.copy2(f, dest)
                elif f.is_dir():
                    shutil.copytree(f, dest, dirs_exist_ok=True)
            print(f"Copied builtin VMDP drivers to {vmdp_dest}")
    # Always ensure custom/ exists
    (DRIVERS_DIR / 'custom').mkdir(exist_ok=True)


init_drivers()

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────

DEFAULTS = {
    "hostname": "win2025-core",
    "password": "",
    "ip": "10.0.0.50",
    "subnet": "255.255.255.0",
    "gateway": "10.0.0.1",
    "dns": "10.0.0.2",
    "timezone": "Romance Standard Time",
    "edition": "Windows Server 2025 Standard Evaluation",
    "partition_scheme": "mbr",
    "ui_language": "en-US",
    "input_locale": "en-US",
    "rdp_enabled": True,
    "nla_enabled": True,
    "ssh_enabled": True,
    "ssh_default_shell": "powershell",
    "winrm_enabled": True,
    "icmp_enabled": False,
    "firewall_ports": [],
    "features": [],
    "additional_users": [],
    "organization": "Homelab",
    "product_key": "",
    "network_location": "Work",
    "protect_your_pc": 3,
}

EDITIONS = [
    "Windows Server 2025 Standard Evaluation",
    "Windows Server 2025 Standard Evaluation (Desktop Experience)",
    "Windows Server 2025 Datacenter Evaluation",
    "Windows Server 2025 Datacenter Evaluation (Desktop Experience)",
]

TIMEZONES = [
    {"value": "Romance Standard Time", "label": "Romance Standard Time (France, Belgique, Espagne)"},
    {"value": "W. Europe Standard Time", "label": "W. Europe Standard Time (Allemagne, Italie, Suisse)"},
    {"value": "GMT Standard Time", "label": "GMT Standard Time (Royaume-Uni, Irlande, Portugal)"},
    {"value": "Central Europe Standard Time", "label": "Central Europe Standard Time (Pologne, Hongrie)"},
    {"value": "Central European Standard Time", "label": "Central European Standard Time (Varsovie)"},
    {"value": "E. Europe Standard Time", "label": "E. Europe Standard Time (Roumanie, Bulgarie)"},
    {"value": "FLE Standard Time", "label": "FLE Standard Time (Finlande, Estonie, Lettonie)"},
    {"value": "GTB Standard Time", "label": "GTB Standard Time (Grece)"},
    {"value": "Russian Standard Time", "label": "Russian Standard Time (Moscou)"},
    {"value": "UTC", "label": "UTC"},
    {"value": "Eastern Standard Time", "label": "Eastern Standard Time (US East)"},
    {"value": "Pacific Standard Time", "label": "Pacific Standard Time (US West)"},
]

LOCALES = [
    {"value": "en-US", "label": "en-US (English, United States)"},
    {"value": "fr-FR", "label": "fr-FR (Francais, France)"},
    {"value": "de-DE", "label": "de-DE (Deutsch, Deutschland)"},
    {"value": "es-ES", "label": "es-ES (Espanol, Espana)"},
    {"value": "it-IT", "label": "it-IT (Italiano, Italia)"},
    {"value": "pt-PT", "label": "pt-PT (Portugues, Portugal)"},
    {"value": "nl-NL", "label": "nl-NL (Nederlands, Nederland)"},
    {"value": "pl-PL", "label": "pl-PL (Polski, Polska)"},
    {"value": "ru-RU", "label": "ru-RU (Russian)"},
    {"value": "ja-JP", "label": "ja-JP (Japanese)"},
]

FEATURES = [
    {"value": "Hyper-V", "label": "Hyper-V"},
    {"value": "Web-Server", "label": "IIS Web Server"},
    {"value": "DNS", "label": "DNS Server"},
    {"value": "DHCP", "label": "DHCP Server"},
    {"value": "AD-Domain-Services", "label": "Active Directory Domain Services"},
    {"value": "Containers", "label": "Windows Containers"},
    {"value": "Failover-Clustering", "label": "Failover Clustering"},
    {"value": "FileAndStorage-Services", "label": "File and Storage Services"},
    {"value": "NET-Framework-45-Core", "label": ".NET Framework 4.5"},
    {"value": "Telnet-Client", "label": "Telnet Client"},
    {"value": "SNMP-Service", "label": "SNMP Service"},
    {"value": "BitLocker", "label": "BitLocker Drive Encryption"},
]

# Component XML attributes (reused everywhere)
COMP_ATTRS = (
    'processorArchitecture="amd64" '
    'publicKeyToken="31bf3856ad364e35" '
    'language="neutral" '
    'versionScope="nonSxS" '
    'xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)

NS = 'urn:schemas-microsoft-com:unattend'


# ──────────────────────────────────────────────────────────────
# XML Generation
# ──────────────────────────────────────────────────────────────

def generate_xml(c):
    """Generate a complete autounattend.xml from a config dict."""
    # Merge defaults for missing keys
    cfg = {**DEFAULTS, **c}

    parts = []
    parts.append('<?xml version="1.0" encoding="utf-8"?>')
    parts.append(f'<unattend xmlns="urn:schemas-microsoft-com:unattend">')
    parts.append('')
    parts.append(_build_windows_pe(cfg))
    parts.append('')
    parts.append(_build_specialize(cfg))
    parts.append('')
    parts.append(_build_oobe(cfg))
    parts.append('')
    parts.append('</unattend>')
    return '\n'.join(parts) + '\n'


def _build_windows_pe(c):
    locale = escape(c['ui_language'])
    input_loc = escape(c['input_locale'])
    edition = escape(c['edition'])
    org = escape(c.get('organization', 'Homelab'))
    product_key = c.get('product_key', '').strip()
    scheme = c.get('partition_scheme', 'mbr')

    # Partition ID for Windows install target
    win_partition = '3' if scheme == 'gpt' else '2'

    product_key_xml = ''
    if product_key:
        product_key_xml = f'''
      <ProductKey>
        <Key>{escape(product_key)}</Key>
        <WillShowUI>Never</WillShowUI>
      </ProductKey>'''

    disk_config = _build_disk_mbr() if scheme == 'mbr' else _build_disk_gpt()

    return f'''  <!-- ============================================================ -->
  <!-- Pass 1: windowsPE - Disk partitioning, image selection       -->
  <!-- ============================================================ -->
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE"
               {COMP_ATTRS}>
      <SetupUILanguage>
        <UILanguage>{locale}</UILanguage>
      </SetupUILanguage>
      <InputLocale>{input_loc}</InputLocale>
      <SystemLocale>{locale}</SystemLocale>
      <UILanguage>{locale}</UILanguage>
      <UserLocale>{locale}</UserLocale>
    </component>

    <component name="Microsoft-Windows-Setup"
               {COMP_ATTRS}>

      <!-- SUSE VMDP VirtIO driver paths - scan all possible drive letters -->
      <DriverPaths>
        <PathAndCredentials wcm:action="add" wcm:keyValue="1">
          <Path>D:\\win10-11-server22\\x64\\pvvx</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="2">
          <Path>D:\\</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="3">
          <Path>E:\\win10-11-server22\\x64\\pvvx</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="4">
          <Path>E:\\</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="5">
          <Path>F:\\win10-11-server22\\x64\\pvvx</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="6">
          <Path>F:\\</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="7">
          <Path>G:\\win10-11-server22\\x64\\pvvx</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="8">
          <Path>G:\\</Path>
        </PathAndCredentials>
      </DriverPaths>

{disk_config}

      <!-- Image selection -->
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>/IMAGE/NAME</Key>
              <Value>{edition}</Value>
            </MetaData>
          </InstallFrom>
          <InstallTo>
            <DiskID>0</DiskID>
            <PartitionID>{win_partition}</PartitionID>
          </InstallTo>
        </OSImage>
      </ImageInstall>

      <UserData>
        <AcceptEula>true</AcceptEula>
        <FullName>Administrator</FullName>
        <Organization>{org}</Organization>{product_key_xml}
      </UserData>
    </component>
  </settings>'''


def _build_disk_mbr():
    return '''      <!-- Disk partitioning (BIOS/MBR) -->
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order>
              <Type>Primary</Type>
              <Size>500</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order>
              <Type>Primary</Type>
              <Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order>
              <PartitionID>1</PartitionID>
              <Label>System</Label>
              <Format>NTFS</Format>
              <Active>true</Active>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order>
              <PartitionID>2</PartitionID>
              <Label>Windows</Label>
              <Format>NTFS</Format>
              <Letter>C</Letter>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>'''


def _build_disk_gpt():
    return '''      <!-- Disk partitioning (UEFI/GPT) -->
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order>
              <Type>EFI</Type>
              <Size>260</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order>
              <Type>MSR</Type>
              <Size>128</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>3</Order>
              <Type>Primary</Type>
              <Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order>
              <PartitionID>1</PartitionID>
              <Format>FAT32</Format>
              <Label>EFI</Label>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order>
              <PartitionID>2</PartitionID>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>3</Order>
              <PartitionID>3</PartitionID>
              <Format>NTFS</Format>
              <Label>Windows</Label>
              <Letter>C</Letter>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>'''


def _build_specialize(c):
    hostname = escape(c['hostname'][:15])
    tz = escape(c['timezone'])
    rdp = c.get('rdp_enabled', True)
    nla = c.get('nla_enabled', True)

    rdp_section = ''
    if rdp:
        rdp_section = f'''
    <component name="Microsoft-Windows-TerminalServices-LocalSessionManager"
               {COMP_ATTRS}>
      <fDenyTSConnections>false</fDenyTSConnections>
    </component>'''

    nla_section = ''
    if rdp and not nla:
        nla_section = f'''
    <component name="Microsoft-Windows-TerminalServices-RDP-WinStationExtensions"
               {COMP_ATTRS}>
      <UserAuthentication>0</UserAuthentication>
    </component>'''

    # Firewall groups
    firewall_groups = []
    if rdp:
        firewall_groups.append(('RemoteDesktop', 'Remote Desktop'))
    if c.get('winrm_enabled', False):
        firewall_groups.append(('WinRM', 'Windows Remote Management'))

    firewall_section = ''
    if firewall_groups:
        groups_xml = '\n'.join(
            f'''        <FirewallGroup wcm:action="add" wcm:keyValue="{key}">
          <Active>true</Active>
          <Group>{group}</Group>
          <Profile>all</Profile>
        </FirewallGroup>'''
            for key, group in firewall_groups
        )
        firewall_section = f'''
    <component name="Networking-MPSSVC-Svc"
               {COMP_ATTRS}>
      <FirewallGroups>
{groups_xml}
      </FirewallGroups>
    </component>'''

    return f'''  <!-- ============================================================ -->
  <!-- Pass 2: specialize - Hostname, timezone, RDP                 -->
  <!-- ============================================================ -->
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup"
               {COMP_ATTRS}>
      <ComputerName>{hostname}</ComputerName>
      <TimeZone>{tz}</TimeZone>
    </component>{rdp_section}{nla_section}{firewall_section}
  </settings>'''


def _build_oobe(c):
    password = escape(c['password'])
    net_loc = escape(c.get('network_location', 'Work'))
    pypc = int(c.get('protect_your_pc', 3))

    # Additional users
    users = c.get('additional_users', [])
    local_accounts_xml = ''
    if users:
        accounts = []
        for u in users[:5]:
            uname = escape(u.get('name', ''))
            upwd = escape(u.get('password', ''))
            ugrp = escape(u.get('group', 'Users'))
            if uname:
                accounts.append(f'''        <LocalAccount wcm:action="add">
          <Name>{uname}</Name>
          <DisplayName>{uname}</DisplayName>
          <Group>{ugrp}</Group>
          <Password>
            <Value>{upwd}</Value>
            <PlainText>true</PlainText>
          </Password>
        </LocalAccount>''')
        if accounts:
            local_accounts_xml = '\n      <LocalAccounts>\n' + '\n'.join(accounts) + '\n      </LocalAccounts>'

    commands = _build_first_logon_commands(c)

    return f'''  <!-- ============================================================ -->
  <!-- Pass 3: oobeSystem - Admin password, auto-config scripts     -->
  <!-- ============================================================ -->
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup"
               {COMP_ATTRS}>

      <UserAccounts>
        <AdministratorPassword>
          <Value>{password}</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>{local_accounts_xml}
      </UserAccounts>

      <AutoLogon>
        <Enabled>true</Enabled>
        <Username>Administrator</Username>
        <Password>
          <Value>{password}</Value>
          <PlainText>true</PlainText>
        </Password>
        <LogonCount>1</LogonCount>
      </AutoLogon>

      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <NetworkLocation>{net_loc}</NetworkLocation>
        <ProtectYourPC>{pypc}</ProtectYourPC>
      </OOBE>

{commands}
    </component>
  </settings>'''


def _build_first_logon_commands(c):
    ip = escape(c['ip'])
    subnet = escape(c['subnet'])
    gateway = escape(c['gateway'])
    dns = escape(c['dns'])

    cmds = []
    order = [0]  # mutable counter

    def add(cmd_line, desc):
        order[0] += 1
        cmds.append(f'''        <!-- {order[0]}. {desc} -->
        <SynchronousCommand wcm:action="add">
          <Order>{order[0]}</Order>
          <CommandLine>{escape(cmd_line)}</CommandLine>
          <Description>{escape(desc)}</Description>
        </SynchronousCommand>''')

    # Always: static IP
    add(
        f'cmd /c netsh interface ip set address "Ethernet" static {ip} {subnet} {gateway}',
        'Set static IP address'
    )

    # Always: DNS
    add(
        f'cmd /c netsh interface ip set dns "Ethernet" static {dns} primary',
        'Set DNS server'
    )

    # Conditional: WinRM
    if c.get('winrm_enabled', False):
        add(
            'powershell -Command "Enable-PSRemoting -Force; Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted -Value $true; Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true"',
            'Enable WinRM'
        )

    # Conditional: SSH
    if c.get('ssh_enabled', False):
        add(
            'powershell -Command "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0; Start-Service sshd; Set-Service -Name sshd -StartupType Automatic"',
            'Install OpenSSH Server'
        )
        # SSH default shell
        shell = c.get('ssh_default_shell', 'powershell')
        if shell == 'powershell':
            shell_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        else:
            shell_path = r"C:\Windows\System32\cmd.exe"
        add(
            f"powershell -Command \"New-ItemProperty -Path 'HKLM:\\SOFTWARE\\OpenSSH' -Name DefaultShell -Value '{shell_path}' -PropertyType String -Force\"",
            'Set SSH default shell'
        )

    # Conditional: Firewall rules
    fw_rules = []
    if c.get('ssh_enabled', False):
        fw_rules.append("New-NetFirewallRule -DisplayName 'SSH' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow")
    if c.get('winrm_enabled', False):
        fw_rules.append("New-NetFirewallRule -DisplayName 'WinRM HTTP' -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow")
        fw_rules.append("New-NetFirewallRule -DisplayName 'WinRM HTTPS' -Direction Inbound -Protocol TCP -LocalPort 5986 -Action Allow")
    if c.get('icmp_enabled', False):
        fw_rules.append("New-NetFirewallRule -DisplayName 'Allow ICMPv4' -Protocol ICMPv4 -IcmpType 8 -Action Allow")
    for port_entry in c.get('firewall_ports', []):
        port = str(port_entry.get('port', ''))
        proto = port_entry.get('protocol', 'TCP').upper()
        if port:
            fw_rules.append(f"New-NetFirewallRule -DisplayName 'Custom {proto}/{port}' -Direction Inbound -Protocol {proto} -LocalPort {port} -Action Allow")

    if fw_rules:
        combined = '; '.join(fw_rules)
        add(f'powershell -Command "{combined}"', 'Open firewall ports')

    # Conditional: Windows features
    for feat in c.get('features', []):
        add(
            f'powershell -Command "Install-WindowsFeature -Name {escape(feat)} -IncludeManagementTools"',
            f'Install {feat}'
        )

    # Always last: disable AutoLogon
    add(
        'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" /v AutoAdminLogon /t REG_SZ /d 0 /f',
        'Disable AutoLogon'
    )

    return '''      <FirstLogonCommands>
''' + '\n'.join(cmds) + '''
      </FirstLogonCommands>'''


# ──────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────

def validate_config(c):
    """Validate config dict, return list of error strings."""
    errors = []
    hostname = c.get('hostname', '')
    if not hostname:
        errors.append('Hostname is required')
    elif len(hostname) > 15:
        errors.append(f'Hostname must be 15 characters or less (currently {len(hostname)})')
    elif not re.match(r'^[a-zA-Z0-9-]+$', hostname):
        errors.append('Hostname must contain only letters, digits, and hyphens')

    if not c.get('password'):
        errors.append('Password is required')

    for field in ('ip', 'subnet', 'gateway', 'dns'):
        val = c.get(field, '')
        if not val:
            errors.append(f'{field} is required')
        elif not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', val):
            errors.append(f'{field} must be a valid IPv4 address')

    return errors


def validate_xml(xml_string):
    """Validate XML string, return list of error dicts with line/severity/message."""
    errors = []

    # Level 1: XML syntax
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        line = getattr(e, 'position', (0, 0))[0] if hasattr(e, 'position') else 0
        errors.append({"line": line, "severity": "error", "message": f"XML syntax error: {e}"})
        return errors

    ns = {'u': NS}

    # Level 2: required structure
    pe = root.find('.//u:settings[@pass="windowsPE"]', ns)
    if pe is None:
        errors.append({"line": 0, "severity": "error", "message": "Missing <settings pass=\"windowsPE\">"})
    else:
        if pe.find('.//u:DiskConfiguration', ns) is None:
            errors.append({"line": 0, "severity": "warning", "message": "Missing DiskConfiguration in windowsPE"})
        if pe.find('.//u:ImageInstall', ns) is None:
            errors.append({"line": 0, "severity": "warning", "message": "Missing ImageInstall in windowsPE"})

    spec = root.find('.//u:settings[@pass="specialize"]', ns)
    if spec is None:
        errors.append({"line": 0, "severity": "error", "message": "Missing <settings pass=\"specialize\">"})
    else:
        cn = spec.find('.//u:ComputerName', ns)
        if cn is None:
            errors.append({"line": 0, "severity": "warning", "message": "Missing ComputerName in specialize"})
        elif cn.text and len(cn.text) > 15:
            errors.append({"line": 0, "severity": "error", "message": f"ComputerName exceeds 15 characters: {cn.text}"})

    oobe = root.find('.//u:settings[@pass="oobeSystem"]', ns)
    if oobe is None:
        errors.append({"line": 0, "severity": "error", "message": "Missing <settings pass=\"oobeSystem\">"})
    else:
        if oobe.find('.//u:AdministratorPassword', ns) is None:
            errors.append({"line": 0, "severity": "warning", "message": "Missing AdministratorPassword in oobeSystem"})

    # Check FirstLogonCommands order
    flc = root.find('.//u:FirstLogonCommands', ns)
    if flc is not None:
        orders = []
        for cmd in flc.findall('.//u:SynchronousCommand', ns):
            o = cmd.find('u:Order', ns)
            if o is not None and o.text:
                orders.append(int(o.text))
        if orders:
            if len(orders) != len(set(orders)):
                errors.append({"line": 0, "severity": "error", "message": "Duplicate Order values in FirstLogonCommands"})
            expected = list(range(1, len(orders) + 1))
            if sorted(orders) != expected:
                errors.append({"line": 0, "severity": "warning", "message": "Order values in FirstLogonCommands are not sequential"})

    return errors


# ──────────────────────────────────────────────────────────────
# XML Parsing (import existing XML)
# ──────────────────────────────────────────────────────────────

def parse_xml(xml_string):
    """Parse an autounattend.xml and extract a config dict."""
    cfg = dict(DEFAULTS)

    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError:
        return cfg

    ns = {'u': NS, 'wcm': 'http://schemas.microsoft.com/WMIConfig/2002/State'}

    def find_text(parent, path, default=''):
        el = parent.find(path, ns)
        return el.text.strip() if el is not None and el.text else default

    # windowsPE
    pe = root.find('.//u:settings[@pass="windowsPE"]', ns)
    if pe is not None:
        cfg['ui_language'] = find_text(pe, './/u:UILanguage', cfg['ui_language'])
        cfg['input_locale'] = find_text(pe, './/u:InputLocale', cfg['input_locale'])

        # Edition
        meta_val = find_text(pe, './/u:ImageInstall//u:Value')
        if meta_val:
            cfg['edition'] = meta_val

        # Partition scheme detection
        disk = pe.find('.//u:DiskConfiguration', ns)
        if disk is not None:
            efi = disk.find('.//u:CreatePartition/[u:Type="EFI"]', ns)
            if efi is not None:
                cfg['partition_scheme'] = 'gpt'
            else:
                cfg['partition_scheme'] = 'mbr'

        # Product key
        pk = find_text(pe, './/u:ProductKey/u:Key')
        if pk:
            cfg['product_key'] = pk

        # Organization
        org = find_text(pe, './/u:Organization')
        if org:
            cfg['organization'] = org

    # specialize
    spec = root.find('.//u:settings[@pass="specialize"]', ns)
    if spec is not None:
        cfg['hostname'] = find_text(spec, './/u:ComputerName', cfg['hostname'])
        cfg['timezone'] = find_text(spec, './/u:TimeZone', cfg['timezone'])

        # RDP
        deny = find_text(spec, './/u:fDenyTSConnections')
        if deny:
            cfg['rdp_enabled'] = deny.lower() == 'false'

        # NLA
        nla = find_text(spec, './/u:UserAuthentication')
        if nla:
            cfg['nla_enabled'] = nla != '0'

    # oobeSystem
    oobe = root.find('.//u:settings[@pass="oobeSystem"]', ns)
    if oobe is not None:
        cfg['password'] = find_text(oobe, './/u:AdministratorPassword/u:Value', cfg['password'])

        net_loc = find_text(oobe, './/u:NetworkLocation')
        if net_loc:
            cfg['network_location'] = net_loc

        pypc = find_text(oobe, './/u:ProtectYourPC')
        if pypc:
            try:
                cfg['protect_your_pc'] = int(pypc)
            except ValueError:
                pass

        # Parse FirstLogonCommands
        cfg['ssh_enabled'] = False
        cfg['winrm_enabled'] = False
        cfg['icmp_enabled'] = False
        cfg['features'] = []

        for cmd_el in oobe.findall('.//u:SynchronousCommand', ns):
            cmd_line = find_text(cmd_el, 'u:CommandLine', '')

            # IP / subnet / gateway
            m = re.search(r'set address "Ethernet" static (\S+) (\S+) (\S+)', cmd_line)
            if m:
                cfg['ip'] = m.group(1)
                cfg['subnet'] = m.group(2)
                cfg['gateway'] = m.group(3)

            # DNS
            m = re.search(r'set dns "Ethernet" static (\S+)', cmd_line)
            if m:
                cfg['dns'] = m.group(1)

            # SSH detection
            if 'OpenSSH.Server' in cmd_line:
                cfg['ssh_enabled'] = True
            if 'DefaultShell' in cmd_line:
                if 'powershell.exe' in cmd_line.lower():
                    cfg['ssh_default_shell'] = 'powershell'
                else:
                    cfg['ssh_default_shell'] = 'cmd'

            # WinRM detection
            if 'Enable-PSRemoting' in cmd_line:
                cfg['winrm_enabled'] = True

            # ICMP detection
            if 'ICMPv4' in cmd_line:
                cfg['icmp_enabled'] = True

            # Features
            m = re.search(r'Install-WindowsFeature\s+-Name\s+(\S+)', cmd_line)
            if m:
                cfg['features'].append(m.group(1))

        # Additional users
        cfg['additional_users'] = []
        for acct in oobe.findall('.//u:LocalAccounts/u:LocalAccount', ns):
            name = find_text(acct, 'u:Name')
            pwd = find_text(acct, 'u:Password/u:Value')
            grp = find_text(acct, 'u:Group', 'Users')
            if name:
                cfg['additional_users'].append({'name': name, 'password': pwd, 'group': grp})

    return cfg


# ──────────────────────────────────────────────────────────────
# Config storage helpers
# ──────────────────────────────────────────────────────────────

def client_timestamp(config):
    """Use browser-provided timestamp if valid, else fall back to server time."""
    ts = config.get('client_timestamp', '')
    if ts and re.match(r'^\d{4}-\d{2}-\d{2}_\d{2}h\d{2}$', ts):
        return ts
    return datetime.now().strftime('%Y-%m-%d_%Hh%M')


def slugify(name):
    s = re.sub(r'[^a-zA-Z0-9]+', '-', name.strip().lower())
    s = s.strip('-')
    return s[:50] if s else 'unnamed'


def list_configs():
    configs = []
    for f in sorted(CONFIGS_DIR.glob('*.json')):
        try:
            data = json.loads(f.read_text())
            configs.append({
                'name': data.get('name', f.stem),
                'slug': f.stem,
                'updated': data.get('updated', ''),
            })
        except (json.JSONDecodeError, OSError):
            pass
    return configs


# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/api/defaults')
def api_defaults():
    return jsonify({
        'defaults': DEFAULTS,
        'editions': EDITIONS,
        'timezones': TIMEZONES,
        'locales': LOCALES,
        'features': FEATURES,
        'driver_sources': _list_driver_sources(),
    })


@app.route('/api/generate', methods=['POST'])
def api_generate():
    config = request.json or {}
    errors = validate_config(config)
    if errors:
        return jsonify({'errors': errors}), 400
    xml = generate_xml(config)
    return jsonify({'xml': xml})


@app.route('/api/download', methods=['POST'])
def api_download():
    config = request.json or {}
    errors = validate_config(config)
    if errors:
        return jsonify({'errors': errors}), 400
    xml = generate_xml(config)
    hostname = config.get('hostname', 'autounattend')[:15]
    timestamp = client_timestamp(config)
    xml_filename = f"autounattend-{hostname}-{timestamp}.xml"
    (XML_DIR / xml_filename).write_text(xml)
    return jsonify({'xml': xml, 'filename': xml_filename})


@app.route('/api/build-iso', methods=['POST'])
def api_build_iso():
    config = request.json or {}
    errors = validate_config(config)
    if errors:
        return jsonify({'errors': errors}), 400

    if not build_lock.acquire(blocking=False):
        return jsonify({'error': 'A build is already in progress'}), 409

    xml = generate_xml(config)
    hostname = config.get('hostname', 'autounattend')[:15]
    timestamp = client_timestamp(config)
    iso_filename = f"autounattend-{hostname}-{timestamp}.iso"
    xml_filename = f"autounattend-{hostname}-{timestamp}.xml"

    # Build driver paths from selected sources
    driver_sources = config.get('driver_sources', [])
    driver_paths = []
    for src in driver_sources:
        src_dir = DRIVERS_DIR / src
        if src_dir.is_dir() and any(src_dir.iterdir()):
            driver_paths.append(str(src_dir))
    driver_paths_arg = ':'.join(driver_paths) if driver_paths else ''

    def generate():
        try:
            yield f"data: === Starting ISO build\n\n"
            yield f"data: Output directory: {OUTPUT_DIR.resolve()}\n\n"
            # Save XML to xml/ directory
            xml_path = XML_DIR / xml_filename
            xml_path.write_text(xml)
            yield f"data: XML saved as {xml_filename}\n\n"
            yield f"data: __XML_FILE__:{xml_filename}\n\n"

            output_path = OUTPUT_DIR / iso_filename
            script = str(Path(__file__).parent / 'build-iso-from-xml.sh')

            cmd = [script, str(xml_path), str(output_path)]
            if driver_paths_arg:
                cmd.append(driver_paths_arg)
                yield f"data: Driver sources: {', '.join(driver_sources)}\n\n"

            yield f"data: === Running: {script}\n\n"
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()

            if proc.returncode == 0:
                yield f"data: === ISO build complete\n\n"
                yield f"data: __ISO_FILE__:{iso_filename}\n\n"
                yield f"data: __DONE_OK__\n\n"
            else:
                yield f"data: === Build failed (exit code {proc.returncode})\n\n"
                yield f"data: __DONE_FAIL__\n\n"
        except Exception as e:
            yield f"data: === ERROR: {e}\n\n"
            yield f"data: __DONE_FAIL__\n\n"
        finally:
            build_lock.release()

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/download-iso/<filename>')
def api_download_iso(filename):
    # Sanitize: only allow expected filename pattern
    if not re.match(r'^autounattend-[\w-]+\.iso$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        return jsonify({'error': 'ISO not found'}), 404
    return send_from_directory(str(OUTPUT_DIR), filename,
                               mimetype='application/octet-stream',
                               as_attachment=True)


@app.route('/api/parse', methods=['POST'])
def api_parse():
    if request.content_type and 'multipart' in request.content_type:
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        xml_string = f.read().decode('utf-8', errors='replace')
    else:
        data = request.json or {}
        xml_string = data.get('xml', '')
    if not xml_string:
        return jsonify({'error': 'No XML content'}), 400
    config = parse_xml(xml_string)
    return jsonify({'config': config})


@app.route('/api/validate', methods=['POST'])
def api_validate():
    data = request.json or {}
    xml_string = data.get('xml', '')
    if not xml_string:
        return jsonify({'error': 'No XML content'}), 400
    errors = validate_xml(xml_string)
    return jsonify({'errors': errors})


@app.route('/api/configs', methods=['GET'])
def api_list_configs():
    return jsonify({'configs': list_configs()})


@app.route('/api/configs', methods=['POST'])
def api_save_config():
    data = request.json or {}
    name = data.get('name', '').strip()
    config = data.get('config')
    if not name or not config:
        return jsonify({'error': 'Name and config required'}), 400

    slug = slugify(name)
    now = datetime.now().isoformat(timespec='seconds')
    filepath = CONFIGS_DIR / f'{slug}.json'

    payload = {
        'name': name,
        'config': config,
        'created': now,
        'updated': now,
    }
    # Preserve original creation date if file exists
    if filepath.exists():
        try:
            existing = json.loads(filepath.read_text())
            payload['created'] = existing.get('created', now)
        except (json.JSONDecodeError, OSError):
            pass

    filepath.write_text(json.dumps(payload, indent=2))
    return jsonify({'slug': slug, 'name': name})


@app.route('/api/configs/<name>', methods=['GET'])
def api_get_config(name):
    slug = slugify(name) if not re.match(r'^[a-z0-9-]+$', name) else name
    filepath = CONFIGS_DIR / f'{slug}.json'
    if not filepath.exists():
        return jsonify({'error': 'Config not found'}), 404
    data = json.loads(filepath.read_text())
    return jsonify(data)


@app.route('/api/configs/<name>', methods=['PUT'])
def api_update_config(name):
    slug = slugify(name) if not re.match(r'^[a-z0-9-]+$', name) else name
    filepath = CONFIGS_DIR / f'{slug}.json'
    if not filepath.exists():
        return jsonify({'error': 'Config not found'}), 404

    data = request.json or {}
    existing = json.loads(filepath.read_text())

    if 'config' in data:
        existing['config'] = data['config']
    if 'name' in data:
        existing['name'] = data['name']
    existing['updated'] = datetime.now().isoformat(timespec='seconds')

    # If renaming, move file
    new_name = data.get('name', '').strip()
    if new_name:
        new_slug = slugify(new_name)
        new_path = CONFIGS_DIR / f'{new_slug}.json'
        if new_slug != slug:
            filepath.unlink()
            filepath = new_path
        existing['name'] = new_name

    filepath.write_text(json.dumps(existing, indent=2))
    return jsonify({'slug': filepath.stem, 'name': existing['name']})


@app.route('/api/configs/<name>', methods=['DELETE'])
def api_delete_config(name):
    slug = slugify(name) if not re.match(r'^[a-z0-9-]+$', name) else name
    filepath = CONFIGS_DIR / f'{slug}.json'
    if not filepath.exists():
        return jsonify({'error': 'Config not found'}), 404
    filepath.unlink()
    return jsonify({'deleted': slug})


# ──────────────────────────────────────────────────────────────
# XML storage routes
# ──────────────────────────────────────────────────────────────

@app.route('/api/xmls')
def api_list_xmls():
    xmls = []
    for f in sorted(XML_DIR.glob('*.xml'), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        xmls.append({
            'name': f.name,
            'size': st.st_size,
            'modified': datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
        })
    return jsonify({'xmls': xmls})


@app.route('/api/download-xml/<filename>')
def api_download_xml(filename):
    if not re.match(r'^autounattend-[\w-]+\.xml$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = XML_DIR / filename
    if not filepath.exists():
        return jsonify({'error': 'XML not found'}), 404
    return send_from_directory(str(XML_DIR), filename,
                               mimetype='application/xml',
                               as_attachment=True)


# ──────────────────────────────────────────────────────────────
# Driver routes
# ──────────────────────────────────────────────────────────────

def _list_driver_sources():
    sources = []
    if not DRIVERS_DIR.is_dir():
        return sources
    for d in sorted(DRIVERS_DIR.iterdir()):
        if not d.is_dir():
            continue
        files = list(d.rglob('*'))
        file_list = [f for f in files if f.is_file()]
        inf_count = sum(1 for f in file_list if f.suffix.lower() == '.inf')
        total_size = sum(f.stat().st_size for f in file_list)
        sources.append({
            'name': d.name,
            'inf_count': inf_count,
            'file_count': len(file_list),
            'total_size': total_size,
        })
    return sources


@app.route('/api/drivers')
def api_list_drivers():
    return jsonify({'sources': _list_driver_sources()})


@app.route('/api/drivers/upload', methods=['POST'])
def api_upload_driver():
    custom_dir = DRIVERS_DIR / 'custom'
    custom_dir.mkdir(exist_ok=True)
    uploaded = []
    allowed_ext = {'.inf', '.sys', '.cat', '.dll'}
    for f in request.files.getlist('files'):
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in allowed_ext:
            continue
        safe_name = re.sub(r'[^\w.-]', '_', f.filename)
        dest = custom_dir / safe_name
        f.save(str(dest))
        uploaded.append(safe_name)
    if not uploaded:
        return jsonify({'error': 'No valid driver files (.inf .sys .cat .dll)'}), 400
    return jsonify({'uploaded': uploaded})


@app.route('/api/drivers/custom/<filename>', methods=['DELETE'])
def api_delete_custom_driver(filename):
    safe = re.sub(r'[^\w.-]', '_', filename)
    filepath = DRIVERS_DIR / 'custom' / safe
    if not filepath.exists():
        return jsonify({'error': 'File not found'}), 404
    filepath.unlink()
    return jsonify({'deleted': safe})


@app.route('/api/drivers/custom')
def api_list_custom_drivers():
    custom_dir = DRIVERS_DIR / 'custom'
    if not custom_dir.is_dir():
        return jsonify({'files': []})
    files = []
    for f in sorted(custom_dir.iterdir()):
        if f.is_file():
            files.append({'name': f.name, 'size': f.stat().st_size})
    return jsonify({'files': files})


# ──────────────────────────────────────────────────────────────
# Windows images routes
# ──────────────────────────────────────────────────────────────

@app.route('/api/images')
def api_list_images():
    images = []
    for f in sorted(IMAGES_DIR.glob('*.iso'), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        images.append({
            'name': f.name,
            'size_gb': round(st.st_size / (1024**3), 2),
            'modified': datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
            'url': f'/images/{f.name}',
        })
    return jsonify({'images': images})


@app.route('/images/<filename>')
def serve_image(filename):
    if not re.match(r'^[\w.-]+\.iso$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = IMAGES_DIR / filename
    if not filepath.exists():
        return jsonify({'error': 'Image not found'}), 404
    return send_from_directory(str(IMAGES_DIR), filename,
                               conditional=True)


@app.route('/api/images/<filename>', methods=['DELETE'])
def api_delete_image(filename):
    if not re.match(r'^[\w.-]+\.iso$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = IMAGES_DIR / filename
    if not filepath.exists():
        return jsonify({'error': 'Image not found'}), 404
    filepath.unlink()
    return jsonify({'deleted': filename})


@app.route('/api/guide')
def api_guide():
    lang = request.args.get('lang', 'en')
    filename = 'GUIDE-fr.md' if lang == 'fr' else 'GUIDE.md'
    guide = Path(__file__).parent / filename
    if not guide.exists():
        return jsonify({'error': f'{filename} not found'}), 404
    return Response(guide.read_text(encoding='utf-8'), mimetype='text/plain; charset=utf-8')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8098, debug=False)
