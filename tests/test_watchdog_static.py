from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / 'scripts' / 'vpnchain-watchdog.sh'


def test_watchdog_has_restart_limit_and_logging():
    script = WATCHDOG.read_text()
    assert 'MAX_RESTARTS="${VPNCHAIN_WATCHDOG_MAX_RESTARTS:-5}"' in script
    assert 'restart limit reached' in script
    assert 'vpnchain-watchdog' in script
    assert '/var/log/vpnchain-watchdog.log' in script


def test_watchdog_checks_real_vpn_interfaces_not_only_container():
    script = WATCHDOG.read_text()
    assert 'awg show' in script
    assert 'try_up_iface awg0' in script
    assert 'try_up_iface awg1' in script
    assert 'repair_dnsmasq' in script
