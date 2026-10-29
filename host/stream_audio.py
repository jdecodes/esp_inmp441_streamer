import socket, struct, wave, math
from array import array
import os
from datetime import datetime
import sounddevice as sd

RECORDING_DIR = r"E:\recordings_from_mcu"

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", 5000))
s.settimeout(1.0)

pcm = array("h")
expected = None
drops = pkts = 0
win = array("h")

SR = 16000
CH = 1

    
print("listening on UDP 5000, Ctrl-C to stop and save out.wav")
try:

    with sd.RawOutputStream(samplerate=SR,
                            channels=CH,dtype="int16", 
                            blocksize=1024,) as stream:
        while True:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                print("no packets (check IP / firewall / wifi)")
                continue
            if len(data) < 6:
                continue
            seq = struct.unpack_from("<I", data)[0]
            if expected is not None and seq != expected:
                drops += (seq - expected) & 0xFFFFFFFF
            expected = seq + 1
            pkts += 1

            chunk = array("h")
            chunk.frombytes(data[4:4 + (len(data) - 4) // 2 * 2])
            
            stream.write(data[4:])
            pcm.extend(chunk)
            win.extend(chunk)

            if len(win) >= 16000:
                rms = math.sqrt(sum(x * x for x in win) / len(win))
                bar = "#" * min(60, int(rms / 100))
                print(f"rms={rms:7.0f} peak={max(map(abs, win)):5d} "
                    f"pkts={pkts} drops={drops} {bar}")
                win = array("h")
except KeyboardInterrupt:
    pass

if pcm:
    mean = sum(pcm) // len(pcm)  # remove DC offset
    out = array("h", (max(-32768, min(32767, x - mean)) for x in pcm))
    
    filename = f"mcu_{datetime.now():%Y-%m-%d_%H-%M}.wav"
    filepath = os.path.join(RECORDING_DIR, filename)
    with wave.open(filepath, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(out.tobytes())
    print(f"saved {filepath}, {len(pcm) / 16000:.1f}s, drops={drops}")