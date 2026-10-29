import threading
import wave
from datetime import datetime
import os
from udp_receiver import END_OF_STREAM
from configs import CHANNEL_COUNT, SAMPLE_WIDTH_BYTES, RECORDING_DIR


class WavWriter(threading.Thread):
    """Takes audio chunks from a queue and appends them to a WAV file
    as they arrive."""

    def __init__(self, input_queue, sample_rate_hz):
        super().__init__()
        self._input_queue = input_queue
        filename = f"mcu_{datetime.now():%Y-%m-%d_%H-%M}.wav"
        filepath = os.path.join(RECORDING_DIR, filename)
        self._filename = filepath
        self._sample_rate_hz = sample_rate_hz
        self.bytes_written = 0

    def run(self):
        with wave.open(self._filename, "wb") as wav_file:
            wav_file.setnchannels(CHANNEL_COUNT)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(self._sample_rate_hz)

            while True:
                audio_chunk = self._input_queue.get()  # waits until data arrives
                if audio_chunk is END_OF_STREAM:
                    break
                wav_file.writeframes(audio_chunk)
                self.bytes_written += len(audio_chunk)
        # Leaving the "with" block closes the file and fixes up the WAV header.
    
    def get_file_name(self):
        return self._filename