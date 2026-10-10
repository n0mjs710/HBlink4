# Public browser access deployment

This document describes a reversible way to publish the Bueno DMR dashboard
through HTTPS without buying a domain, installing an app on visitor devices, or
forwarding an inbound TCP port on the router. It is an operational proposal;
the commands below have not been run on the production Raspberry Pi.

## Recommended topology

Install Tailscale on the Raspberry Pi only. Tailscale Funnel accepts public
HTTPS requests at the Pi's `*.ts.net` name and relays them to an isolated
public-only dashboard listener on `127.0.0.1:8081`. Visitors open that HTTPS
URL in an ordinary mobile or desktop browser; they do not need a Tailscale
account or client. Funnel uses TCP and does not proxy, bind, or alter the DMR
listener on UDP 62031.

```text
Visitor browser
    | HTTPS :443
    v
Tailscale Funnel relay === encrypted Tailscale connection ===> Raspberry Pi
                                                               | HTTP loopback :8081
                                                               v
                                                     Public-only dashboard app

Operator browser (tailnet member)
    | HTTPS :8443 (private Serve)
    v
Main dashboard + authenticated /admin on 127.0.0.1:8080

WPSD hotspot ---------------- DMR/HBP UDP :62031 ------------> HBlink4
```

The main dashboard stays on its existing LAN listener at port `8080`, where
operators can use it privately. During deployment, enable the separate public
listener with `BUENODMR_PUBLIC_WEB=1`; the code binds it only to
`127.0.0.1:8081` and serves only `/`, the public CSS/JS assets, the sanitized
snapshot API, and its WebSocket. It does not register `/admin`, raw telemetry,
or API documentation. Funnel targets this loopback-only listener. Keep the
dashboard event socket and radio service configuration unchanged.

The authenticated `/admin` remains private to the LAN dashboard listener and
can be reached over the tailnet with Tailscale Serve on HTTPS port `8443`. The
public Funnel app does not serve an Admin link or Admin routes. Keep the
dashboard's Secure cookie setting enabled while using the HTTPS Serve URL. Put
these settings in a systemd drop-in for `hblink4-dash`, not in a tracked unit
or live config:

```ini
Environment=BUENODMR_PUBLIC_WEB=1
Environment=BUENODMR_ADMIN_COOKIE_SECURE=1
```

Funnel and Serve terminate browser TLS and proxy HTTP to loopback, so set the
flag explicitly rather than relying on the backend request scheme.

## Preconditions

Before an operator changes production, record the current branch/commit,
service states, listener sockets, live dashboard bind/port, and the locations
of backups. Do not print or copy live configuration contents into Git or logs.
Confirm the current DMR listener is UDP 62031 and that both HBlink4 services
are healthy. Confirm no router port forwarding is added for TCP 8080 or TCP
443 or 8443. This plan does not require opening inbound router ports; the Pi
needs outbound connectivity to Tailscale coordination/relay services.

The Raspberry Pi OS documentation identifies Raspberry Pi OS as Debian based
and uses APT for package installation. Tailscale's current Linux install
documentation explicitly lists Raspberry Pi OS. Check the installed OS and
architecture first; do not perform a major OS upgrade as part of this change.

Funnel prerequisites are a Tailscale account and enrolled Pi, MagicDNS, HTTPS
certificates enabled for the tailnet, and the `funnel` node attribute in the
tailnet policy. Funnel currently supports public HTTPS ports 443, 8443, and
10000. Its generated name is under the tailnet's `*.ts.net` domain. Funnel is
documented as beta and traffic is subject to non-configurable bandwidth limits.
Use Tailscale 1.52 or later for the current CLI syntax in this procedure, and
check the installed version with `tailscale version` before configuring it.

## Proposed setup commands

Run only during a scheduled deployment after taking and checking the required
backups. The first command is Tailscale's official Linux installer; use its
manual package instructions instead if the operator does not want to pipe the
vendor script to a shell.

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo systemctl enable --now tailscaled
sudo tailscale up
```

Complete the sign-in in the URL shown by `tailscale up`. In the Tailscale admin
console, enable MagicDNS and HTTPS certificates, then authorize Funnel for the
tailnet. Do not enable Tailscale SSH for this publication task.

After confirming the main dashboard is listening on its expected private/LAN
address, the public-only listener is at `127.0.0.1:8081`, and the Secure-cookie
environment setting is active, configure a private Serve URL for Admin and a
public Funnel URL for visitors:

```sh
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8080
sudo tailscale funnel --bg --https=443 http://127.0.0.1:8081
sudo tailscale serve status
sudo tailscale funnel status
```

Use the exact HTTPS URL printed by `tailscale funnel status` as the public
address. The `--bg` option makes Funnel resume after reboot and Tailscale
service restarts. Keep `tailscaled` enabled at boot. Do not create a separate
systemd tunnel unit unless a later operational requirement demonstrates a need;
the Tailscale daemon persists this Funnel configuration itself.

## Verification

Verify these items from outside the home network, using a browser that does not
have Tailscale installed:

1. The published `https://<device>.<tailnet>.ts.net` URL loads with a valid
   browser certificate.
2. Public page data, API calls, and WebSocket updates work over HTTPS.
3. The reviewed public fields contain no secrets or private operational data.
4. The public URL returns 404 for `/admin`, `/api/config`, `/api/repeaters`,
   `/docs`, and `/openapi.json`; only the reviewed visitor routes work.
5. The private Serve URL on HTTPS port `8443` opens `/admin`, and login cookies
   have the `Secure` attribute. Requests from a device outside the tailnet
   cannot reach that private URL.
6. `ss -lnt` shows the public listener bound only to `127.0.0.1:8081`, and
   there is no router port-forward for TCP 8080, 443, or 8443.
7. `ss -lun` and service status confirm HBlink4 still listens on UDP 62031 and
   the HBlink4 service remains active.
8. During an approved reboot check, the dashboard and `tailscaled` return,
   `tailscale funnel status` reports the same public URL, `tailscale serve
   status` reports the private Admin URL, and an external browser can load the
   public URL again. The DMR service and hotspot connectivity must be checked
   independently; Funnel is not a substitute for a radio regression check.

Do not claim reboot persistence verified until item 7 has actually been
performed on the production device.

## Rollback

To stop all public Funnel routes immediately while leaving Tailscale installed
and avoiding any restart of HBlink4:

```sh
sudo tailscale funnel reset
sudo tailscale serve reset
sudo tailscale funnel status
sudo systemctl is-active hblink4 hblink4-dash
```

Reset removes the public Funnel and private Serve configurations from the Pi.
Visitor access ends after the relay configuration updates. Until Serve is
re-enabled, operators use the existing trusted LAN/SSH workflow for Admin.

Remove only the new `BUENODMR_PUBLIC_WEB` and
`BUENODMR_ADMIN_COOKIE_SECURE` lines from the `hblink4-dash` drop-in, reload
systemd, and restart `hblink4-dash`. This disables the loopback-only public
listener and restores the previous Admin cookie behavior. Never restore a
checked-in sample over a live config. Do not restart `hblink4` for a web-only
rollback. Finish by checking that `hblink4` is active and UDP 62031 remains
bound.

Keeping the Tailscale package installed after `funnel reset` is the least
disruptive rollback. Removing the package or deleting the tailnet authorization
is unnecessary to turn off publication and could disrupt other Tailscale
administration workflows.

## Risks and limits

- Funnel makes the target reachable to anyone on the Internet. The `*.ts.net`
  URL is an address, not an access-control mechanism.
- Funnel targets a separate app listener with an explicit route allowlist. The
  authenticated Admin remains private to the tailnet Serve URL.
- A Funnel or dashboard failure affects web access only. Keep the web service
  operationally independent from `hblink4`; never make a web restart a
  prerequisite for UDP 62031 service.
- Publication depends on the Pi being powered on, online, enrolled in the same
  tailnet, and running both `tailscaled` and the local dashboard. A home ISP
  outage or a Tailscale account/policy change makes the URL unavailable.
- Funnel bandwidth is limited by Tailscale and is not configurable. This is a
  fit for a small community status dashboard, not a high-volume general-purpose
  website.
- The generated URL depends on the tailnet DNS name and Pi node name. Record it
  after setup and avoid renaming either without planning URL communication.

## Official references

- [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
- [Tailscale `funnel` CLI reference](https://tailscale.com/docs/reference/tailscale-cli/funnel)
- [Tailscale Linux installation](https://tailscale.com/docs/install/linux)
- [What firewall ports Tailscale uses](https://tailscale.com/docs/reference/faq/firewall-ports)
- [Raspberry Pi OS documentation](https://www.raspberrypi.com/documentation/computers/os.html)
- [Project dashboard documentation](../dashboard/README.md)
- [BuenoDMR administration and Secure cookie setting](buenodmr-admin.md)
