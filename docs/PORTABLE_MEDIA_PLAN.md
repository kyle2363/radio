# Connected speech and offline music contract

`portable_media.py` defines `radio.portable.media_plan`, version 1, independently
of VLC, HTMLAudioElement, Android or any playback library. This is a reference
contract/resolver. Existing desktop DJ generation and media delivery continue
through their existing authenticated playback services; the portable capabilities
endpoint honestly reports `native_speech_generation:false`.

A plan has an opaque playback `context_id` and ordered segments. A `local_song`
segment holds a device-owned `local_id`. An optional `speech` segment holds a
stable resource ID, raw-content SHA-256, byte length, expiry and context ID.
It declares `on_unavailable:skip`. Credentials, private media grants and server
paths do not belong in the plan. A future authorized speech adapter will resolve
resources using the existing generated-media service and its explicit leases.
Its content hash is separate from that service's domain-prefixed resource ID.

Local song selection, gapless transitions and crossfade do not depend on speech.
While connected a client can prefetch authorized speech without creating a play
event or consuming its local music queue. Expired, obsolete or failed speech is
omitted and music proceeds. An offline client schedules no new server generation,
talk shows, or automatic device-TTS replacement. It may use a fully cached clip
only when bytes, length, checksum, expiry and playback context all match; partial
or invalid cached audio is skipped. The reference resolver tests these cases.

The next native phase must add a scoped request/media adapter, resource lease and
cancellation tests, authentication, contextual invalidation, and audible scheduling
checks before enabling native server-generated speech. It must not reuse desktop
session ownership as authority to interrupt another device's independent music.
