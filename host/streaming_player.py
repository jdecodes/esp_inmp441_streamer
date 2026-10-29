import queue

import sounddevice as sd

from udp_receiver import END_OF_STREAM
from configs import CHANNEL_COUNT, SAMPLE_WIDTH_BYTES

MS_PER_SECOND = 1000

# Wait for this much audio before starting playback (cushion against jitter).
PREFILL_MS = 200
# If the buffer grows beyond this, drop the oldest audio (handles clock drift).
MAX_BUFFER_MS = 5000


class StreamingPlayer:
    """Plays audio from a queue. The sound card calls _on_audio_needed()
    whenever it wants more samples; we hand it what we have buffered."""

    def __init__(self, input_queue, sample_rate_hz):
        self._input_queue = input_queue
        self._sample_rate_hz = sample_rate_hz

        bytes_per_ms = sample_rate_hz * SAMPLE_WIDTH_BYTES * CHANNEL_COUNT / MS_PER_SECOND
        self._prefill_bytes = int(PREFILL_MS * bytes_per_ms)
        self._max_buffer_bytes = int(MAX_BUFFER_MS * bytes_per_ms)
        self._bytes_per_ms = bytes_per_ms

        self._buffer = bytearray()   # audio waiting to be played
        self._is_playing = False     # False while (re)filling the cushion
        self._stream = None
        self.underrun_count = 0      # times we ran out of audio

    @property
    def buffered_ms(self):
        return len(self._buffer) / self._bytes_per_ms

    def start(self):
        self._stream = sd.RawOutputStream(
            samplerate=self._sample_rate_hz,
            channels=CHANNEL_COUNT,
            dtype="int16",
            callback=self._on_audio_needed,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def _move_queue_into_buffer(self):
        while True:
            try:
                audio_chunk = self._input_queue.get_nowait()
            except queue.Empty:
                break
            if audio_chunk is END_OF_STREAM:
                continue
            self._buffer += audio_chunk

        # Too much waiting audio: keep only the newest part.
        if len(self._buffer) > self._max_buffer_bytes:
            excess = len(self._buffer) - self._max_buffer_bytes
            #print(f"not matching get rid of excess {excess}")
            del self._buffer[:excess]

    def _on_audio_needed(self, outdata, frame_count, time_info, status):
        bytes_needed = frame_count * SAMPLE_WIDTH_BYTES * CHANNEL_COUNT
        silence = bytes(bytes_needed)

        self._move_queue_into_buffer()

        # Still building the initial cushion: play silence.
        if not self._is_playing:
            if len(self._buffer) < self._prefill_bytes:
                outdata[:] = silence
                #print(f"bytes_needed {bytes_needed} return silence")
                return
            self._is_playing = True

        # Not enough audio: play what we have, pad with silence, rebuild cushion.
        if len(self._buffer) < bytes_needed:
            self.underrun_count += 1
            available = len(self._buffer)
            outdata[:available] = bytes(self._buffer)
            outdata[available:] = silence[available:]
            self._buffer.clear()
            self._is_playing = False
            #print(f"bytes_needed {bytes_needed} available {available}, underrun")
            return

        # Normal case: hand over exactly what the sound card asked for.
        outdata[:] = bytes(self._buffer[:bytes_needed])
        del self._buffer[:bytes_needed]