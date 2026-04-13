from aws_cli_tools.targets import classify_target, is_instance_id, is_ipv4_address


def test_is_instance_id_accepts_ec2_instance_ids():
    assert is_instance_id("i-0123456789abcdef0") is True


def test_is_instance_id_rejects_non_instance_ids():
    assert is_instance_id("instance-0123456789abcdef0") is False


def test_is_ipv4_address_accepts_valid_address():
    assert is_ipv4_address("10.0.0.15") is True


def test_is_ipv4_address_rejects_out_of_range_octet():
    assert is_ipv4_address("256.0.0.1") is False


def test_classify_target_prefers_instance_ids_then_ip_then_name():
    assert classify_target("i-0123456789abcdef0") == "instance_id"
    assert classify_target("10.0.0.15") == "ip"
    assert classify_target("my-app-web-01") == "name"

