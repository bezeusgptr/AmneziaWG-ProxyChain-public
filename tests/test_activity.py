from vpnchain.activity import merge_peer_activity, parse_wg_dump


def test_parse_wg_dump_peer_lines():
    dump = """server-private\tserver-public\t51820\toff\npeerpub1\t(none)\t203.0.113.9:2222\t10.77.0.3/32\t1710000000\t1234\t5678\t25\npeerpub2\t(none)\t(none)\t10.77.0.4/32\t0\t0\t0\toff\n"""
    parsed = parse_wg_dump(dump)

    assert set(parsed) == {'peerpub1', 'peerpub2'}
    assert parsed['peerpub1'].endpoint == '203.0.113.9:2222'
    assert parsed['peerpub1'].latest_handshake == 1710000000
    assert parsed['peerpub1'].rx == 1234
    assert parsed['peerpub1'].tx == 5678
    assert parsed['peerpub2'].endpoint is None
    assert parsed['peerpub2'].latest_handshake is None


def test_merge_peer_activity_unknown_gracefully():
    peers = [{'name': 'alice', 'public_key': 'peerpub1'}, {'name': 'bob', 'public_key': 'missing'}]
    activity = parse_wg_dump('peerpub1\t(none)\t198.51.100.5:1\t10.77.0.3/32\t1710000000\t10\t20\t25\n')

    merged = merge_peer_activity(peers, activity)

    assert merged[0]['activity']['endpoint'] == '198.51.100.5:1'
    assert merged[0]['activity']['rx'] == 10
    assert merged[1]['activity']['endpoint'] is None
    assert merged[1]['activity']['online'] is None
