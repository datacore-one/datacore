"""Tests for ledger.keys - Ed25519 keypairs, sign/verify, public registry."""

import multiprocessing

import yaml

from ledger.keys import ensure_keypair, sign, verify


def test_keypair_created_once_and_registered(tmp_path):
    reg = tmp_path / "registry.yaml"
    vk1 = ensure_keypair("winston", keys_dir=tmp_path / "keys", registry_path=reg)
    vk2 = ensure_keypair("winston", keys_dir=tmp_path / "keys", registry_path=reg)
    assert vk1 == vk2 and vk1 in reg.read_text()


def test_private_key_mode_0600(tmp_path):
    ensure_keypair("winston", keys_dir=tmp_path / "k", registry_path=tmp_path / "r.yaml")
    assert oct((tmp_path / "k" / "winston.key").stat().st_mode & 0o777) == "0o600"


def test_sign_verify_roundtrip(tmp_path):
    ensure_keypair("miles", keys_dir=tmp_path / "k", registry_path=tmp_path / "r.yaml")
    sig = sign("miles", b"payload", keys_dir=tmp_path / "k")
    assert verify("miles", b"payload", sig, registry_path=tmp_path / "r.yaml")
    assert not verify("miles", b"tampered", sig, registry_path=tmp_path / "r.yaml")


def test_verify_and_ensure_keypair_survive_actors_null(tmp_path):
    reg = tmp_path / "r.yaml"
    reg.write_text("actors: null\n")
    assert not verify("miles", b"payload", "00" * 64, registry_path=reg)
    vk = ensure_keypair("miles", keys_dir=tmp_path / "k", registry_path=reg)
    assert vk in reg.read_text()


def test_verify_and_ensure_keypair_survive_invalid_yaml(tmp_path):
    reg = tmp_path / "r.yaml"
    reg.write_text("{[")
    assert not verify("miles", b"payload", "00" * 64, registry_path=reg)
    vk = ensure_keypair("miles", keys_dir=tmp_path / "k", registry_path=reg)
    assert vk in reg.read_text()


def test_verify_and_ensure_keypair_survive_toplevel_list(tmp_path):
    reg = tmp_path / "r.yaml"
    reg.write_text("- foo\n- bar\n")
    assert not verify("miles", b"payload", "00" * 64, registry_path=reg)
    vk = ensure_keypair("miles", keys_dir=tmp_path / "k", registry_path=reg)
    assert vk in reg.read_text()


# --- concurrent cold-start locking -------------------------------------------


def _mp_ensure_keypair_worker(keys_dir_str, registry_path_str, actor):
    """Module-level worker (must be picklable) -- each process calls
    ensure_keypair for the SAME brand-new actor against the shared
    tmp_path keys_dir/registry, racing the cold-start (no key file yet)."""
    from pathlib import Path

    from ledger.keys import ensure_keypair

    return ensure_keypair(actor, keys_dir=Path(keys_dir_str), registry_path=Path(registry_path_str))


def test_ensure_keypair_concurrent_coldstart_same_actor(tmp_path):
    """4 processes cold-start the same never-before-seen actor at once.

    Without the per-actor flock + double-checked existence check, each
    process could see no key file, generate its own keypair, and race the
    key file / registry -- leaving some signer using a private key that no
    longer matches what's registered. With the lock, exactly one keypair
    wins and every process (including the losers of the race) ends up
    agreeing on it.
    """
    keys_dir = tmp_path / "keys"
    registry_path = tmp_path / "registry.yaml"
    actor = "coldstart"
    workers = 4

    args = [(str(keys_dir), str(registry_path), actor) for _ in range(workers)]
    with multiprocessing.Pool(workers) as pool:
        results = pool.starmap(_mp_ensure_keypair_worker, args)

    assert len(set(results)) == 1, f"processes disagreed on verify key: {set(results)}"
    winner_vk = results[0]

    key_path = keys_dir / f"{actor}.key"
    key_hex = key_path.read_text().strip()

    # Re-running sequentially afterward must not perturb the settled key.
    vk_again = ensure_keypair(actor, keys_dir=keys_dir, registry_path=registry_path)
    assert vk_again == winner_vk
    assert key_path.read_text().strip() == key_hex

    registry = yaml.safe_load(registry_path.read_text())
    assert registry["actors"] == {actor: winner_vk}

    sig = sign(actor, b"payload", keys_dir=keys_dir)
    assert verify(actor, b"payload", sig, registry_path=registry_path)
