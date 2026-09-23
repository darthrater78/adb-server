import pytest

import adb_client
import github_client


@pytest.mark.parametrize("text", [
    "octocat/hello-world",
    "https://github.com/octocat/hello-world",
    "github.com/octocat/hello-world.git",
    "https://www.github.com/octocat/hello-world/releases/latest",
    "git@github.com:octocat/hello-world.git",
])
def test_parse_repo_reference_accepts(text):
    assert github_client.parse_repo_reference(text) == ("octocat", "hello-world")


@pytest.mark.parametrize("text", ["", "octocat", "../x", "octo cat/repo", "owner/..", "a/b;rm"])
def test_parse_repo_reference_rejects(text):
    with pytest.raises(github_client.GithubError):
        github_client.parse_repo_reference(text)


@pytest.mark.parametrize("addr", ["192.168.1.50:37251", "10.0.0.2:5555", "127.0.0.1:5555"])
def test_validate_addr_accepts_private(addr):
    assert adb_client._validate_addr(addr) == addr


@pytest.mark.parametrize("addr", [
    "-s:1", "8.8.8.8:5555", "phone.local:5555", "192.168.1.50", "192.168.1.50:0",
    "192.168.1.50:70000", "192.168.1.50:abc",
])
def test_validate_addr_rejects(addr):
    with pytest.raises(adb_client.AdbError):
        adb_client._validate_addr(addr)


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "", "12 456"])
def test_pairing_code_rejects(code):
    with pytest.raises(adb_client.AdbError):
        adb_client._validate_pairing_code(code)
