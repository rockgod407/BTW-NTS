# nettest

Comprehensive network & AV protocol test suite. Tests connectivity, performance, security, and long-form AV protocol stability (NDI, Dante, sACN, Art-Net, TCNet, Pro DJ Link) between machines on your network.

## Quick Install (Mac)

```bash
curl -fsSL https://raw.githubusercontent.com/rockgod407/BTW-NTS/main/install.sh | bash
```

Then restart your terminal and verify:

```bash
nettest doctor
```

## Manual Install

```bash
python3 -m pip install --upgrade pip
python3 -m pip install --user git+https://github.com/rockgod407/BTW-NTS.git
```

If `nettest` isn't found after install, add the Python user bin to your PATH:

```bash
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

(Replace `3.9` with your Python version if different.)

## Usage

### Check Dependencies

```bash
nettest doctor          # Check everything
nettest doctor --fix    # Auto-install missing pip dependencies
```

### Run Network Tests

```bash
nettest                            # Run all standard network tests
nettest -t http -t dns             # Run only HTTP and DNS tests
nettest --quick                    # Skip performance tests
nettest -o report.html             # Export HTML report
```

### Discover AV Devices

```bash
nettest av-discover                # Find all NDI, Dante, sACN, Art-Net devices
```

### End-to-End AV Testing (Two Machines)

Start the **receiver** on Machine B:

```bash
nettest receive --protocol ndi --duration 5m
```

It prints a session ID and the exact sender command. Run that on **Machine A**:

```bash
nettest send --protocol ndi --session <SESSION_ID> --duration 5m
```

Supported protocols: `ndi`, `sacn`, `dante`, `udp`

#### With Presets

```bash
# List all available presets and bandwidth requirements
nettest presets
nettest presets --protocol ndi
nettest presets --protocol dante

# Send with a specific preset
nettest send --protocol ndi --preset 1080p60 --session <ID> --duration 1h
nettest send --protocol sacn --preset arena --session <ID> --duration 30m
nettest send --protocol dante --preset concert-foh --session <ID> --duration 30m
```

### Long-Form Stream Monitoring

Monitor an existing NDI source on the network:

```bash
nettest ndi --source "My Camera" --duration 2h --fps 59.94
```

Monitor sACN universes:

```bash
nettest sacn --universes 1,2,3 --duration 1h
```

### Signal Generators & Stress Tests

```bash
# Generate test traffic
nettest generate --protocol ndi --preset 1080p60 --duration 10m
nettest generate --protocol sacn --preset arena --duration 1h

# Multi-stream stress test
nettest stress --profile heavy --duration 30m
nettest stress --profile max-gigabit --duration 1h
```

### Bandwidth Calculator

```bash
nettest calc --protocol ndi --preset 1080p60 --count 8
nettest calc --protocol dante --preset concert-foh --count 1
```

### Utilities

```bash
nettest scan --host 192.168.1.1 --start 1 --end 1024    # Port scan
nettest traceroute --host 8.8.8.8                        # Traceroute
nettest lookup --domain example.com --type MX             # DNS lookup
```

## NDI Setup

NDI requires the native NDI library (`libndi.dylib`). The easiest way to get it:

```bash
pip3 install ndi-python
```

Or install the [NDI SDK](https://ndi.video/tools/ndi-sdk/) directly. Run `nettest doctor` to verify NDI is detected.

## Report Export

```bash
nettest -o report.json     # JSON
nettest -o report.csv      # CSV
nettest -o report.html     # Self-contained dark-theme HTML report
```

## Protocols Tested

| Protocol | Type | Tests |
|----------|------|-------|
| **NDI** | Video over IP | Discovery, long-form stream monitoring, verified E2E send/receive |
| **Dante** | Audio over IP | mDNS discovery, PTP sync monitoring, bandwidth simulation |
| **sACN (E1.31)** | DMX over Ethernet | Discovery, universe monitoring, verified E2E send/receive |
| **Art-Net / MA-Net** | DMX over Ethernet | ArtPoll discovery, DMX stream monitoring |
| **TCNet** | Show control | Node discovery, timecode monitoring |
| **Pro DJ Link** | DJ equipment | Device discovery, beat sync monitoring |
| **HTTP/HTTPS** | Web | Connectivity, response time, TLS validation |
| **TCP** | Transport | Port scanning, connectivity |
| **UDP** | Transport | Connectivity, verified E2E testing |
| **ICMP** | Network | Ping, traceroute |
| **DNS** | Name resolution | Record lookups, response time |
| **NTP** | Time sync | Clock offset, stratum check |
| **TLS/Security** | Security | Certificate validation, cipher suites |
| **Performance** | Load | Bandwidth, latency under load |
