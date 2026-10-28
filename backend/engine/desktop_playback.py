"""Legacy desktop controls address one logical session, never a raw VLC player."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import stat
import threading
from uuid import uuid4

from db.playback_store import ConflictError


class LazyNativeOutput:
    """Reading idle desktop state must not load VLC or require its installation."""
    _defaults = {"get_state": "stopped", "get_time": 0, "get_length": 0,
                 "get_position": 0, "get_current_song_id": None, "get_volume": 70,
                 "get_eq_preset": "off", "is_playing": False, "is_talk_playing": False}

    def __init__(self, factory):
        self._factory, self._output = factory, None
        self._lock = threading.RLock()
        self._closed = False
        self._preferences = {}

    def start(self, snapshot):
        with self._lock:
            if self._closed:
                return False
            if self._output is None:
                self._output = self._factory()
                for name, value in self._preferences.items():
                    getattr(self._output, name)(value)
            output = self._output
        return output.start(snapshot)

    def close(self):
        with self._lock:
            self._closed = True
            output, self._output = self._output, None
        if output is not None:
            output.close()

    dispose = close

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        def call(*args, **kwargs):
            with self._lock:
                output = self._output
                if output is None:
                    if name in ('set_volume', 'set_eq_preset', 'set_normalization_enabled', 'set_talk_volume'):
                        self._preferences[name] = args[0]
                    return self._defaults.get(name)
            return getattr(output, name)(*args, **kwargs)
        return call


class DesktopPlayback:
    def __init__(self, service_factory, state_path_factory=None):
        self._service_factory = service_factory
        self._state_path_factory, self._state_path = state_path_factory, None
        self._remembered = None
        self._lock = threading.RLock()
        self._request = ContextVar('radio_desktop_request', default=None)

    @contextmanager
    def scope(self, *, session_id=None, owner=None):
        bindings = []
        if session_id is not None:
            if not owner:
                raise PermissionError('Playback owner capability required')
            service = self._service_factory()
            service.attach(session_id, owner)
            bindings.append(DesktopBinding(service, session_id, owner, desktop_only=False))
        token = self._request.set(bindings)
        try:
            yield
        finally:
            self._request.reset(token)

    def scoped_binding(self):
        bindings = self._request.get()
        return bindings[0] if bindings else None

    def binding(self):
        request = self._request.get()
        if request:
            return request[0]
        with self._lock:
            service = self._service_factory()
            sid = service._vlc_session
            newly_created = False
            if sid is None:
                saved = self._read_checkpoint()
                if saved is not None:
                    try:
                        state = service.store.snapshot(saved['session_id'], saved['owner_key'])
                        if state['mode'] == 'vlc':
                            service.attach(saved['session_id'], saved['owner_key'])
                            sid, owner = saved['session_id'], saved['owner_key']
                            self._remembered = (self._path(), sid, owner)
                    except (KeyError, ValueError, PermissionError):
                        pass
                sid = service._vlc_session
            if sid is None:
                created = service.create(mode='vlc')
                sid, owner = created['session']['id'], created['owner_key']
                newly_created = True
            else:
                owner = service._owners[sid]
            binding = DesktopBinding(service, sid, owner)
            if newly_created:
                binding.context.queue.request_rebuild()
            self.remember(service, sid, owner)
        if request is not None:
            request.append(binding)
        return binding

    def _path(self):
        if self._state_path is None:
            self._state_path = Path(self._state_path_factory()).absolute()
        return self._state_path

    @staticmethod
    def _valid_checkpoint(value):
        return (isinstance(value, dict) and set(value) == {'session_id', 'owner_key'}
                and isinstance(value['session_id'], str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['session_id']) is not None
                and isinstance(value['owner_key'], str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,512}', value['owner_key']) is not None)

    def _read_checkpoint(self):
        if self._state_path_factory is None:
            return None
        try:
            path = self._path()
            if path.is_symlink():
                return None
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, 'rb') as source:
                opened = os.fstat(source.fileno())
                current = path.lstat()
                if (not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(current.st_mode)
                        or not os.path.samestat(opened, current) or opened.st_size > 16384):
                    return None
                content = source.read(16385)
                if len(content) > 16384 or path.is_symlink():
                    return None
            value = json.loads(content.decode('utf-8'))
            return value if self._valid_checkpoint(value) else None
        except (OSError, ValueError, TypeError, UnicodeError):
            return None

    def remember(self, service, session_id, owner):
        """Privately checkpoint the selected native controller after authorization."""
        if self._state_path_factory is None:
            return False
        value = {'session_id': session_id, 'owner_key': owner}
        if not self._valid_checkpoint(value):
            return False
        with self._lock:
            try:
                state = service.store.snapshot(session_id, owner)
                if state['mode'] != 'vlc' or service._vlc_session != session_id:
                    return False
                path = self._path()
                if path.is_symlink():
                    return False
                identity = (path, session_id, owner)
                if self._remembered == identity:
                    return True
                path.parent.mkdir(parents=True, exist_ok=True)
                content = json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii')
                if len(content) > 16384:
                    return False
                temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
                descriptor = os.open(temporary, flags, 0o600)
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                if path.is_symlink():
                    return False
                os.replace(temporary, path)
                self._remembered = identity
                return True
            except (OSError, ValueError, TypeError, PermissionError, KeyError):
                return False

    get_player = binding

    def claim(self):
        binding = self.binding()
        return {'session': binding.service.snapshot(binding.session_id, binding.owner),
                'owner_key': binding.owner, 'client_id': None}


class DesktopBinding:
    def __init__(self, service, session_id, owner, *, desktop_only=True):
        self.service, self.session_id, self.owner = service, session_id, owner
        self.context = service.context_for(session_id, owner)
        self._desktop_only = desktop_only

    def _state(self):
        state = self.service.store.snapshot(self.session_id, self.owner)
        if self._desktop_only and (state['mode'] != 'vlc' or self.service._vlc_session != self.session_id):
            raise ConflictError('This desktop session moved to another output; refresh controls')
        return state

    def _control(self, name, *args):
        runtime = self.service.coordinator._runtime(self.session_id, self.owner)
        with runtime.transport_lock:
            self._state()
            return getattr(self.service, name)(self.session_id, self.owner, *args)

    def play_song(self, song_id, filepath=None, session_id=None):
        state = self._state()
        result = self.service.select_song(self.session_id, self.owner, song_id, uuid4().hex, state['revision'])
        return not result.get('cancelled', False)

    def next(self):
        state = self._state()
        return self.service.next(self.session_id, self.owner, uuid4().hex, state['revision'])

    def select_pick(self, pick):
        state = self._state()
        plan = self.context.queue.prepare_pick(pick)
        return self.service.select_prepared(self.session_id, self.owner, plan, uuid4().hex, state['revision'])

    def stop(self):
        state = self._state()
        return self.service.stop(self.session_id, self.owner, uuid4().hex, state['revision'])

    def pause(self):
        return self._control('pause')

    def resume(self):
        return self._control('resume')

    def reactivate(self):
        runtime = self.service.coordinator._runtime(self.session_id, self.owner)
        with runtime.transport_lock:
            state = self._state()
            result = self.service.resume_interrupted(self.session_id, self.owner, uuid4().hex, state['revision'])
            if result.get('status') == 'paused':
                return self.service.resume(self.session_id, self.owner)
            return result

    def get_state(self):
        state = self._state()
        if state.get('status') in ('interrupted', 'awaiting_output', 'paused'):
            return state['status']
        return self.context.port.get_state()

    def get_current_song_id(self):
        state = self._state()
        song = self.context.port.get_current_song_id()
        if song is None and state.get('status') in ('interrupted', 'awaiting_output', 'paused'):
            items, index = state['items'], state['active_index']
            if index < len(items) and items[index].get('kind') == 'song':
                return items[index].get('song_id')
        return song

    def get_time(self):
        state = self._state()
        if state.get('status') in ('interrupted', 'awaiting_output'):
            return state.get('position_ms', 0)
        return self.context.port.get_time()

    def seek(self, position_ms):
        return self._control('seek', position_ms)

    def set_volume(self, volume):
        return self._control('set_volume', volume)

    def set_eq_preset(self, preset):
        return self._control('set_eq', preset)

    def set_normalization_enabled(self, enabled):
        return self._control('set_normalization', enabled)

    @property
    def _current_session_id(self):
        return self._state()['history_session_id']

    def feedback(self, kind, *, advance=False):
        runtime = self.service.coordinator._runtime(self.session_id, self.owner)
        with runtime.transport_lock:
            state = self._state()
            items, index = state['items'], state['active_index']
            if index >= len(items) or not items[index].get('started'):
                return {'accepted': False, 'session': state}
            item = items[index]
            return self.service.observe(self.session_id, self.owner, uuid4().hex,
                state['instance_id'], item['item_id'], kind,
                position_ms=self.context.port.get_time(), native=state['mode'] == 'vlc',
                client_id=state.get('output_client_id'), auto_advance=advance)

    def __getattr__(self, name):
        if name in LazyNativeOutput._defaults:
            return getattr(self.context.port, name)
        raise AttributeError(name)


class SessionSleepTimer:
    """Keep the historical timer interface bound to a logical session."""
    def __init__(self, binding):
        self.binding = binding

    def start(self, minutes, fade_seconds=30, switch_to_chill=True):
        b = self.binding
        return b.service.set_sleep_timer(b.session_id, b.owner, minutes,
            fade_seconds=fade_seconds, switch_to_chill=switch_to_chill)

    def cancel(self):
        b = self.binding
        return b.service.cancel_sleep_timer(b.session_id, b.owner)

    def get_status(self):
        b = self.binding
        timer = b.service.get_sleep_timer(b.session_id, b.owner)
        return {'active': timer is not None, 'remaining_seconds': 0, **(timer or {})}
