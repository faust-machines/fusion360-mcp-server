#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Bridges the Fusion 360 MCP add-in from Windows localhost to the Docker/WSL2 container network.

.DESCRIPTION
    The Fusion MCP add-in binds to 127.0.0.1 on Windows. Docker containers reach the Windows
    host via host.docker.internal, which the add-in never sees.

    This script:
      1. Creates netsh portproxy rules to forward each Docker bridge adapter IP:PORT -> 127.0.0.1:PORT
      2. Adds a Windows Firewall inbound rule scoped to those Docker bridge subnets only

    Binding to specific adapter IPs (not 0.0.0.0) means the LAN and VPN adapters never see
    the port, even if the firewall rule were misconfigured.

    Run once. Both rules are persistent (survive reboots). Re-running is safe (idempotent).

.PARAMETER Port
    Port the Fusion MCP add-in is listening on. Default: 9876.

.PARAMETER Remove
    Remove the portproxy and firewall rules instead of adding them.

.EXAMPLE
    .\fusion-mcp-bridge.ps1
    .\fusion-mcp-bridge.ps1 -Port 9876
    .\fusion-mcp-bridge.ps1 -Remove
#>
param(
    [int]$Port = 9876,
    [switch]$Remove
)

$FirewallRuleName = "Fusion MCP - Docker Container Bridge (port $Port)"

# Discover Docker/WSL2 bridge adapter IPs at runtime.
# Only the 172.16-31.x range is used to avoid including LAN/VPN adapters.
# Compute proper network addresses (mask host bits) so Windows Firewall accepts them.
function Get-NetworkAddress([string]$ip, [int]$prefix) {
    $bytes = [System.Net.IPAddress]::Parse($ip).GetAddressBytes()
    $mask = [uint32]([uint32]::MaxValue -shl (32 - $prefix))
    $ipInt = ([uint32]$bytes[0] -shl 24) -bor ([uint32]$bytes[1] -shl 16) -bor ([uint32]$bytes[2] -shl 8) -bor [uint32]$bytes[3]
    $netInt = $ipInt -band $mask
    $net = [System.Net.IPAddress]::new([byte[]]@(($netInt -shr 24) -band 0xFF, ($netInt -shr 16) -band 0xFF, ($netInt -shr 8) -band 0xFF, $netInt -band 0xFF))
    return "$net/$prefix"
}

$DockerAdapterIPs = @(
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -match "^172\.(1[6-9]|2[0-9]|3[01])\." } |
    ForEach-Object { Get-NetworkAddress $_.IPAddress $_.PrefixLength }
)
if (-not $DockerAdapterIPs) {
    Write-Warning "No Docker bridge adapters found in 172.16-31.x range. Falling back to 172.16.0.0/12."
    $DockerAdapterIPs = @("172.16.0.0/12")
}

if ($Remove) {
    Write-Host "Removing Fusion MCP bridge rules for port $Port..." -ForegroundColor Yellow

    netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 2>$null
    $removeIPs = @(
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -match "^172\.(1[6-9]|2[0-9]|3[01])\." } |
        ForEach-Object { $_.IPAddress }
    )
    foreach ($ip in $removeIPs) {
        netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=$ip 2>$null
    }
    Write-Host "  Portproxy rules removed (or were not present)"

    $rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
    if ($rule) {
        Remove-NetFirewallRule -DisplayName $FirewallRuleName
        Write-Host "  Firewall rule removed"
    } else {
        Write-Host "  Firewall rule was not present"
    }

    Write-Host "Done." -ForegroundColor Green
    exit 0
}

Write-Host "Setting up Fusion MCP bridge for port $Port..." -ForegroundColor Cyan
Write-Host ""

# Step 1: portproxy rules (bind to the host's adapter IP, not the network address)
$DockerHostIPs = @(
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -match "^172\.(1[6-9]|2[0-9]|3[01])\." } |
    ForEach-Object { $_.IPAddress }
)
if (-not $DockerHostIPs) { $DockerHostIPs = @() }

$adapterList = $DockerHostIPs -join ", "
Write-Host "[1/3] Adding portproxy rules ($adapterList -> 127.0.0.1:$Port)..."
$proxyOk = $true
foreach ($adapterIP in $DockerHostIPs) {
    netsh interface portproxy add v4tov4 `
        listenport=$Port `
        listenaddress=$adapterIP `
        connectport=$Port `
        connectaddress=127.0.0.1
    $pattern = [regex]::Escape($adapterIP)
    $check = netsh interface portproxy show v4tov4 | Select-String $pattern
    if ($check) {
        Write-Host "  OK: ${adapterIP}:${Port} -> 127.0.0.1:${Port}" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: rule for $adapterIP may not have been created" -ForegroundColor Yellow
        $proxyOk = $false
    }
}
if (-not $proxyOk) {
    Write-Host "  Run 'netsh interface portproxy show v4tov4' to inspect" -ForegroundColor Yellow
}

# Step 2: Firewall rule scoped to Docker subnets only
Write-Host "[2/3] Adding Windows Firewall inbound rule..."
$existing = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Rule already exists - removing and recreating to ensure correct config"
    Remove-NetFirewallRule -DisplayName $FirewallRuleName
}

New-NetFirewallRule `
    -DisplayName $FirewallRuleName `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress ($DockerAdapterIPs -join ",") `
    -Action Allow `
    -Profile Any | Out-Null

$rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if ($rule) {
    Write-Host "  OK: Firewall rule created" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Firewall rule creation failed" -ForegroundColor Red
}

# Step 3: Verify the add-in is listening
Write-Host "[3/3] Checking if Fusion MCP add-in is listening on port $Port..."
$listening = netstat -ano | Select-String "127\.0\.0\.1:$Port.*LISTENING"
if ($listening) {
    Write-Host "  OK: Add-in appears active on 127.0.0.1:$Port" -ForegroundColor Green
} else {
    Write-Host "  NOTE: Nothing on 127.0.0.1:$Port yet - start Fusion and enable the MCP add-in first" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Bridge setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Open Fusion 360 and enable the MCP add-in (if not already)"
Write-Host "  2. Confirm the add-in port matches: $Port"
Write-Host "     If it differs, re-run: .\fusion-mcp-bridge.ps1 -Port <actual-port>"
Write-Host "  3. From inside the devcontainer, test connectivity:"
Write-Host "     python3 -c ""import socket; s=socket.socket(); s.settimeout(3); s.connect(('host.docker.internal',$Port)); print('OK'); s.close()"""
Write-Host "  4. Configure your MCP client to use devcontainer/fusion-mcp-wrapper.sh"
Write-Host "     (see devcontainer/README.md for client config examples)"
Write-Host ""
Write-Host "To remove all rules: .\fusion-mcp-bridge.ps1 -Remove"
