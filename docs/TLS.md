# LAN HTTPS

Voice input needs `getUserMedia`, which browsers only allow in a secure context (HTTPS or `localhost`). The stack therefore serves the app over HTTPS on the LAN.

Default is **option A**: Caddy's built-in CA (`tls internal`) issues a certificate for the Pi's hostname, and each device trusts Caddy's root CA once. Options B and C are sketched at the end; the Caddyfile already switches between them with `JYJ_TLS`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `JYJ_HOSTNAME` | `recipes.home.arpa` | Name devices type into the browser |
| `JYJ_LAN_IP` | empty | Also serve `https://<ip>` (see [IP access](#ip-access)) |
| `JYJ_TLS` | `internal` | `internal` (A), `dns` (B), `tailscale` (C) |

HTTP on :80 redirects to HTTPS. HSTS is deliberately **not** sent, so switching options later never locks browsers out.

## 1. Start the stack and export the CA

```sh
cp .env.example .env   # set JYJ_HOSTNAME, passwords
make up
make ca-export         # writes ./caddy-root.crt (git-ignored) and prints its SHA-256 fingerprint
```

The CA and issued certs live in the `caddy-data` volume (`/data` in the web container). They survive restarts and rebuilds. `docker compose down -v` or deleting that volume creates a **new** CA, and every device has to trust it again.

`caddy-root.crt` is the public certificate only; the private key never leaves the volume. It is still a trust anchor, so only hand it to your own devices.

## 2. Point devices at the hostname

`.home.arpa` is reserved for home networks (RFC 8375), so it never collides with a real domain. Give the Pi a DHCP reservation first so its IP stays fixed. Then pick one:

- **Router DNS**: add a local DNS / static host entry `recipes.home.arpa -> <Pi IP>`. Every device on the Wi-Fi picks it up. Best option if the router supports it.
- **Pi-hole / AdGuard Home**: Local DNS -> DNS Records -> `recipes.home.arpa` -> `<Pi IP>`.
- **Hosts file** (laptops only; phones can't edit it): add `<Pi IP> recipes.home.arpa` to `/etc/hosts` or `C:\Windows\System32\drivers\etc\hosts`.
- **mDNS alternative**: the Pi already answers as `<pi-hostname>.local` via Avahi. Set `JYJ_HOSTNAME=<pi-hostname>.local` and skip DNS setup. Works on iOS, macOS and recent Android; some Android builds and Windows setups resolve `.local` unreliably.

Check from a laptop: `curl --cacert caddy-root.crt https://recipes.home.arpa/api/healthz` should print `{"status":"ok"}`.

### IP access

Setting `JYJ_LAN_IP=192.168.1.50` makes Caddy also issue an internal cert with that IP as a SAN, so `https://192.168.1.50` works on devices that trust the CA. Tradeoff: the cert is bound to that IP, so a DHCP change breaks it until you update `.env` and restart; bookmarks and the PWA origin also differ from the hostname one (separate cookies/storage). Use it as a fallback when DNS is not set up, not as the main URL.

## 3. Trust the CA on each device

Send `caddy-root.crt` to the device (AirDrop, email to yourself, or serve it once from a laptop). Before trusting, compare its SHA-256 fingerprint with what `make ca-export` printed.

### iOS / iPadOS (Safari and every iOS browser)

1. Open the `.crt` file. iOS shows "Profile Downloaded".
2. Settings -> General -> VPN & Device Management -> the "Caddy Local Authority" profile -> Install.
3. Settings -> General -> About -> **Certificate Trust Settings** -> enable full trust for "Caddy Local Authority". Without this step Safari still warns.

### Android (Chrome)

1. Settings -> Security (or Security & privacy) -> More security settings -> **Encryption & credentials** -> **Install a certificate** -> **CA certificate**.
2. Accept the warning, pick `caddy-root.crt`.
3. Menu names vary by vendor; searching Settings for "CA certificate" finds it.

Chrome and most Android browsers trust user-installed CAs.

### Firefox for Android

Firefox uses its own store and ignores user CAs by default. Enable: Settings -> About Firefox -> tap the logo 5 times to unlock the debug menu, then Settings -> Secret Settings -> **Use third party CA certificates** (sets `security.enterprise_roots.enabled`). Desktop Firefox: `about:config` -> `security.enterprise_roots.enabled = true`, or import under Settings -> Privacy & Security -> Certificates.

### macOS / Windows / Linux desktops

- macOS: open the file in Keychain Access (System keychain), then set "When using this certificate" to Always Trust.
- Windows: double-click -> Install Certificate -> Local Machine -> Trusted Root Certification Authorities.
- Debian/Ubuntu: copy to `/usr/local/share/ca-certificates/caddy-root.crt` and run `sudo update-ca-certificates`.

## 4. Verify

1. Open `https://recipes.home.arpa`: padlock, no warning.
2. Check a secure context. On a desktop, open DevTools console and run `window.isSecureContext` (must be `true`). On a phone, use remote debugging: Safari Web Inspector (iPhone: Settings -> Apps -> Safari -> Advanced -> Web Inspector, then Mac Safari -> Develop menu) or `chrome://inspect` on a desktop with the Android phone on USB debugging.
3. The voice button stops showing the "needs HTTPS" message.

Troubleshooting:

- **Warning still shown on iOS**: the Certificate Trust Settings toggle (step 3) is off.
- **Worked before, now warns everywhere**: the `caddy-data` volume was recreated, so there is a new CA. Re-run `make ca-export` and reinstall.
- **Name does not resolve**: the phone uses a DNS server that isn't the router (Private DNS on Android, iCloud Private Relay, a VPN). Turn that off for the home Wi-Fi.

## Option B: owned domain with DNS-01

No device installs. Caddy gets a real Let's Encrypt cert through the DNS provider's API; the A record points at the Pi's **private** IP, so nothing is reachable from the internet. Caveat: the hostname is published in Certificate Transparency logs.

1. Build the web image with the provider plugin, e.g. in a Caddy builder stage: `xcaddy build --with github.com/caddy-dns/cloudflare`.
2. `.env`: `JYJ_TLS=dns`, `JYJ_HOSTNAME=recipes.example.com`, `JYJ_DNS_PROVIDER=cloudflare`, `JYJ_DNS_API_TOKEN=<zone-scoped token>`.
3. Create the A record `recipes.example.com -> <Pi LAN IP>`. Some routers block DNS answers that point at private IPs (DNS rebinding protection); allowlist the domain if so.

Scope the token to DNS edit on that single zone only.

## Option C: Tailscale HTTPS

Each device runs the Tailscale app; also gives remote access later.

1. Install Tailscale on the Pi, enable MagicDNS and HTTPS certificates in the admin console.
2. Mount the tailscaled socket into the web container (`/var/run/tailscale/tailscaled.sock`) via a compose override.
3. `.env`: `JYJ_TLS=tailscale`, `JYJ_HOSTNAME=<pi>.<tailnet>.ts.net`, and set `WEB_BIND` to the Pi's Tailscale IP if it should only be reachable over the tailnet.

Tailscale-issued certs are also logged in CT, under the `ts.net` tailnet name.
