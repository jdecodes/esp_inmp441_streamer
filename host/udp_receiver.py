import socket
import struct
import threading

# ---- packet layout sent by the ESP32 ----
# packet = [ header | audio data ]
#   header     = 4 bytes: sequence number (uint32, little-endian)
#   audio data = N samples, each 2 bytes (int16)
HEADER_SIZE_BYTES = 4
HEADER_FORMAT = "<I"  # little-endian unsigned 32-bit int

MAX_PACKET_SIZE_BYTES = 4096
SOCKET_TIMEOUT_SEC = 0.5   # lets the loop notice stop() even when no packets arrive
LISTEN_ADDRESS = "0.0.0.0"

# Pushed into every queue when the receiver stops, so consumers know to finish.
END_OF_STREAM = None


class UdpReceiver(threading.Thread):
    """Receives UDP packets, strips the header, and copies the audio
    into every output queue (one queue per consumer)."""

    def __init__(self, port, output_queues):
        super().__init__(daemon=True)
        self._port = port
        self._output_queues = output_queues
        self._stop_requested = threading.Event()
        self.packets_received = 0
        self.packets_lost = 0

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((LISTEN_ADDRESS, self._port))
        sock.settimeout(SOCKET_TIMEOUT_SEC)

        expected_seq = None

        while not self._stop_requested.is_set():
            try:
                packet, sender_addr = sock.recvfrom(MAX_PACKET_SIZE_BYTES)
            except socket.timeout:
                continue

            if len(packet) <= HEADER_SIZE_BYTES:
                continue  # no audio in this packet

            header = packet[:HEADER_SIZE_BYTES]
            audio_data = packet[HEADER_SIZE_BYTES:]

            (seq,) = struct.unpack(HEADER_FORMAT, header)
            self.packets_received += 1

            # Gap in sequence numbers = lost packets.
            # seq < expected means the ESP32 rebooted and restarted counting.
            if expected_seq is not None and seq > expected_seq:
                self.packets_lost += seq - expected_seq
                print(f"lost packet(s) expected {expected_seq} got {seq}")
            expected_seq = seq + 1

            for output_queue in self._output_queues:
                output_queue.put(audio_data)

        sock.close()
        for output_queue in self._output_queues:
            output_queue.put(END_OF_STREAM)

    def stop(self):
        self._stop_requested.set()