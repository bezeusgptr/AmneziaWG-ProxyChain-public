# Agent install checklist for VPN Chain v2

Use this checklist when validating a fresh two-server install. Treat all generated WireGuard/AmneziaWG private keys and client configs as secrets: never paste them into chat, issue trackers, logs, or public docs.

## Roles

- **AM / exit server**: public UDP `51821`, runs `awg-am` and accepts the RU uplink as a client peer.
- **RU / entry server**: public UDP `51820`, runs `awg-ru`; end-user clients connect to `awg0`, and non-RU traffic is routed through AM via `awg1`.

## Fresh install flow

On both servers:

```bash
apt-get update
apt-get install -y docker.io docker-compose docker-cli
systemctl enable --now docker || true
```

Optional host module attempt:

```bash
cd /opt/AmneziaWG-ProxyChain
bash install_kernel_module.sh || echo "kernel module unavailable; container userspace fallback may be used"
```

Do not block a functional smoke test only because the host kernel module is unavailable on images without matching headers. Containers can run with the `amneziawg-go` userspace fallback if `/dev/net/tun` and Docker are available.

On AM / exit:

```bash
cd /opt/AmneziaWG-ProxyChain
scripts/vpnchain-bootstrap.sh --role am --apply
scripts/vpnchain-bootstrap.sh \
  --role am \
  --generate-ru-uplink \
  --am-endpoint <AM_PUBLIC_HOST_OR_IP>:51821 \
  --output /etc/vpnchain/ru-awg1.conf \
  --apply
```

Copy `/etc/vpnchain/ru-awg1.conf` securely to the RU server as `/etc/vpnchain/ru-awg1.conf`, mode `0600`. This file contains a private key.

On RU / entry:

```bash
cd /opt/AmneziaWG-ProxyChain
scripts/vpnchain-bootstrap.sh \
  --role ru \
  --ru-uplink-conf /etc/vpnchain/ru-awg1.conf \
  --server-endpoint <RU_PUBLIC_HOST_OR_IP>:51820 \
  --apply
```

## Client profile smoke test on RU

```bash
cd /opt/AmneziaWG-ProxyChain
python3 -m vpnchain.cli --db /var/lib/vpnchain/vpnchain.sqlite init-db
set -a
. /etc/vpnchain/vpnchain.env
set +a
python3 -m vpnchain.cli \
  --db /var/lib/vpnchain/vpnchain.sqlite \
  peer add smoke-client \
  --platform generic \
  --output /var/lib/vpnchain/generated/smoke-client.conf
```

Required checks:

- generated client `Address` is in `10.8.0.0/24` (for example `10.8.0.3/32`), matching RU `awg0` address `10.8.0.1/24`;
- generated config has real `PublicKey` and `Endpoint`, not placeholders;
- generated config is outside the Git checkout and has mode `0600`;
- client public key is present in the CLI JSON output and can be applied to live `awg0`.

Apply a generated client public key to live runtime for smoke testing:

```bash
CLIENT_PUBLIC_KEY=<client-public-key-from-peer-add-json>
CLIENT_ADDRESS=<client-address-from-peer-add-json>
docker exec awg-ru awg set awg0 peer "$CLIENT_PUBLIC_KEY" allowed-ips "$CLIENT_ADDRESS"
```

For persistence across container recreation, store client public keys in `/etc/vpnchain/vpnchain.env` slots (`CLIENT_PUB_KEY`, `CLIENT2_PUB_KEY`, ...), then rerun the RU bootstrap command.

## Verification

AM:

```bash
docker ps
docker exec awg-am awg show awg0
```

RU:

```bash
docker ps
docker exec awg-ru awg show awg0
docker exec awg-ru awg show awg1
docker exec awg-ru ping -c 3 10.9.0.1
stat -c '%a %U:%G %n' /etc/vpnchain/vpnchain.env /etc/vpnchain/ru-awg1.conf
```

Expected:

- `awg-am` and `awg-ru` are up/healthy;
- RU `awg1` has a recent handshake with AM;
- AM `awg0` has RU peer `10.9.0.2/32`;
- end-user client peers on RU `awg0` use `10.8.0.0/24` addresses;
- sensitive files are mode `0600`.

## Before commit or release

Run locally:

```bash
pytest -q
python3 -m vpnchain.cli repo-check .
```

The repository must not contain generated client configs, private keys, `.env` files, SQLite databases, local test server IPs, chat IDs, usernames, or machine-specific local paths.
