import queue
import time
import os

from streaming_player import StreamingPlayer
from udp_receiver import UdpReceiver
from wav_writer import WavWriter

from configs import (UDP_PORT, 
                SAMPLE_RATE_HZ, RECORDING_DIR, SAMPLE_WIDTH_BYTES,
                STATS_INTERVAL_SEC, CHANNEL_COUNT)


def init_check():
    os.makedirs(RECORDING_DIR, exist_ok=True)

def main():
    # One queue per consumer. The receiver copies each chunk into both.
    init_check()
    player_queue = queue.Queue()
    wav_queue = queue.Queue()

    receiver = UdpReceiver(UDP_PORT, [player_queue, wav_queue])
    player = StreamingPlayer(player_queue, SAMPLE_RATE_HZ)
    wav_writer = WavWriter(wav_queue, SAMPLE_RATE_HZ)

    player.start()
    wav_writer.start()
    receiver.start()
    print(f"listening on UDP {UDP_PORT}, Ctrl-C to stop")

    BYTES_PER_SECOND_OF_AUDIO = SAMPLE_RATE_HZ * SAMPLE_WIDTH_BYTES * CHANNEL_COUNT

    start_time = time.monotonic()   # before the while loop
    try:
        while True:
            time.sleep(STATS_INTERVAL_SEC)
            wall_clock_sec = time.monotonic() - start_time
            audio_received_sec = wav_writer.bytes_written / BYTES_PER_SECOND_OF_AUDIO
            print(f"wall={wall_clock_sec:.1f}s  audio_received={audio_received_sec:.1f}s  "
                f"ratio={audio_received_sec / wall_clock_sec:.3f}")     
                   
            print(f"received={receiver.packets_received} "
                  f"lost={receiver.packets_lost} "
                  f"underruns={player.underrun_count} "
                  f"buffered={player.buffered_ms:.0f}ms "
                  f"wav_bytes={wav_writer.bytes_written}")
    except KeyboardInterrupt:
        pass

    receiver.stop()
    receiver.join()      # receiver sends END_OF_STREAM to both queues
    wav_writer.join()    # writer finishes the file and closes it
    player.stop()
    print(f"saved {wav_writer.get_file_name()}")


if __name__ == "__main__":
    main()