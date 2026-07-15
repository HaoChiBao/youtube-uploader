"""Phone-verification proxy via YouTube longUploadsStatus."""

from __future__ import annotations

from uploader.channel_info import (
    AuthorizedChannelInfo,
    is_channel_verified,
    get_authorized_channel_info,
)


def test_is_channel_verified_allowed():
    assert is_channel_verified("allowed") is True
    assert is_channel_verified("ALLOWED") is True
    assert is_channel_verified(" allowed ") is True


def test_is_channel_verified_not_allowed():
    assert is_channel_verified("eligible") is False
    assert is_channel_verified("disallowed") is False
    assert is_channel_verified("") is False
    assert is_channel_verified(None) is False


def test_get_authorized_channel_info_reads_long_uploads_status(monkeypatch):
    class FakeChannels:
        def list(self, **kwargs):
            assert kwargs.get("part") == "snippet,status"
            assert kwargs.get("mine") is True

            class Exec:
                def execute(self):
                    return {
                        "items": [
                            {
                                "id": "UCabc",
                                "snippet": {
                                    "title": "Demo Channel",
                                    "customUrl": "@demo",
                                },
                                "status": {"longUploadsStatus": "allowed"},
                            }
                        ]
                    }

            return Exec()

    class FakeYoutube:
        def channels(self):
            return FakeChannels()

    monkeypatch.setattr(
        "uploader.youtube_client._require_google_libs",
        lambda: (None, None, None, lambda *a, **k: FakeYoutube(), None, None),
    )

    info = get_authorized_channel_info("token.json", creds=object())
    assert isinstance(info, AuthorizedChannelInfo)
    assert info.youtube_channel_id == "UCabc"
    assert info.title == "Demo Channel"
    assert info.custom_url == "@demo"
    assert info.long_uploads_status == "allowed"
    assert is_channel_verified(info.long_uploads_status) is True


def test_channel_out_verified_field():
    from api.schemas import ChannelOut, PublishConfigOut, TokenStatus

    out = ChannelOut(
        id="demo",
        name="Demo",
        token_path="t.json",
        registry_path="r.txt",
        auth=TokenStatus(has_token=True, valid=True, status="ok"),
        publish=PublishConfigOut(),
        long_uploads_status="allowed",
        verified=True,
    )
    assert out.verified is True
    assert out.long_uploads_status == "allowed"
