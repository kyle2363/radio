import base64
import json

import pytest

from mobile_identity import ServerIdentity


class Protector:
    def protect(self, value):
        return value

    def unprotect(self, value):
        return value


def payload(envelope):
    return json.loads(base64.urlsafe_b64decode(envelope['payload'] + '=' * (-len(envelope['payload']) % 4)))


def test_publication_revision_persists_and_public_identity_tracks_it(tmp_path):
    identity = ServerIdentity(tmp_path / 'identity', protector=Protector())
    first = payload(identity.publish_descriptor('https://first.trycloudflare.com', 1))
    second = payload(identity.publish_descriptor('https://first.trycloudflare.com', 1))
    assert second['publication_revision'] > first['publication_revision'] >= 1
    restored = ServerIdentity(tmp_path / 'identity', protector=Protector())
    assert payload(restored.descriptor('https://first.trycloudflare.com', 1))['publication_revision'] == second['publication_revision']
    assert payload(restored.publish_descriptor('https://second.trycloudflare.com', 2))['publication_revision'] > second['publication_revision']


def test_legacy_identity_without_revision_is_upgraded(tmp_path):
    path = tmp_path / 'identity'
    identity = ServerIdentity(path, protector=Protector())
    old = json.loads(path.read_bytes())
    old.pop('publication_revision', None)
    path.write_text(json.dumps(old))
    restored = ServerIdentity(path, protector=Protector())
    assert restored.public_key_pin == identity.public_key_pin
    assert payload(restored.publish_descriptor('https://first.trycloudflare.com', 1))['publication_revision'] >= 1


def test_failed_revision_persistence_never_issues_descriptor(tmp_path):
    identity = ServerIdentity(tmp_path / 'identity', protector=Protector())
    identity._save = lambda: (_ for _ in ()).throw(OSError('disk unavailable'))
    with pytest.raises(OSError):
        identity.publish_descriptor('https://first.trycloudflare.com', 1)


@pytest.mark.parametrize('revision', [-1, True, '1'])
def test_invalid_stored_revision_requires_repair(tmp_path, revision):
    path = tmp_path / 'identity'
    ServerIdentity(path, protector=Protector())
    stored = json.loads(path.read_bytes())
    stored['publication_revision'] = revision
    path.write_text(json.dumps(stored))
    with pytest.raises(RuntimeError):
        ServerIdentity(path, protector=Protector())
