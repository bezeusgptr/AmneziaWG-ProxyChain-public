from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RU_ENTRYPOINT = ROOT / 'server-ru' / 'entrypoint.sh'


def test_ru_config_is_always_rendered_from_current_peer_environment():
    script = RU_ENTRYPOINT.read_text()

    assert 'envsubst < /config/awg0.conf.template > /etc/amnezia/amneziawg/awg0.conf' in script
    assert "if [ ! -s /etc/amnezia/amneziawg/awg0.conf ]" not in script
    assert 'Preserving existing /etc/amnezia/amneziawg/awg0.conf' not in script
    assert script.index('envsubst < /config/awg0.conf.template') < script.index(
        'awg-quick up awg0'
    )
