from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class PeerActivity:
    public_key: str
    latest_handshake: int | None = None
    rx: int | None = None
    tx: int | None = None
    endpoint: str | None = None

    @property
    def online(self) -> bool | None:
        if self.latest_handshake is None or self.latest_handshake <= 0:
            return False
        return (datetime.now(timezone.utc).timestamp() - self.latest_handshake) < 180


def parse_wg_dump(text: str) -> dict[str, PeerActivity]:
    """Parse `wg show ... dump` or `awg show ... dump` output by public key.

    Peer lines are tab-separated as:
    public_key, preshared_key, endpoint, allowed_ips, latest_handshake,
    transfer_rx, transfer_tx, persistent_keepalive.
    Interface header lines have fewer fields and are ignored.
    """
    activities: dict[str, PeerActivity] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) < 8:
            continue
        public_key, _psk, endpoint, _allowed_ips, handshake, rx, tx, *_rest = fields
        activities[public_key] = PeerActivity(
            public_key=public_key,
            latest_handshake=_int_or_none(handshake),
            rx=_int_or_none(rx),
            tx=_int_or_none(tx),
            endpoint=None if endpoint in ('', '(none)') else endpoint,
        )
    return activities


def load_activity_from_command(
    interface: str,
    *,
    tool: str = 'wg',
    command_prefix: list[str] | None = None,
    timeout: float = 2.0,
) -> dict[str, PeerActivity]:
    """Load peer activity with a small, testable shell boundary.

    Returns an empty mapping if the tool is unavailable or the command fails so
    the WebUI can display unknown/offline state without requiring root.

    ``command_prefix`` lets active-server WebUIs read activity from wrappers such
    as ``docker exec awg-ru awg`` while keeping the default host-tool behavior.
    """
    cmd = [*(command_prefix or [tool]), 'show', interface, 'dump']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    return parse_wg_dump(result.stdout)


def merge_peer_activity(peers: Iterable[dict[str, Any]], activities: dict[str, PeerActivity]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for peer in peers:
        item = dict(peer)
        activity = activities.get(str(peer.get('public_key') or ''))
        item['activity'] = {
            'latest_handshake': activity.latest_handshake if activity else None,
            'rx': activity.rx if activity else None,
            'tx': activity.tx if activity else None,
            'endpoint': activity.endpoint if activity else None,
            'online': activity.online if activity else None,
        }
        merged.append(item)
    return merged


def _int_or_none(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
