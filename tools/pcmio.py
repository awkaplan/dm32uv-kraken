"""16-bit mono PCM in and out for the AFSK / sounding tools: WAV or raw signed 16-bit little endian."""
import pathlib
import struct
import sys
import wave


def unpack(raw):
    """Raw s16le bytes -> list of ints (a trailing odd byte is dropped)."""
    return list(struct.unpack("<%dh" % (len(raw) // 2), raw[:len(raw) // 2 * 2]))


def pack(samples):
    """Samples (ints, or floats truncated) -> s16le bytes, clamped to the 16-bit range."""
    return struct.pack("<%dh" % len(samples), *[max(-32768, min(32767, int(v))) for v in samples])


def read(path, rate=8000, strict_rate=False):
    """Samples of a .wav (mono 16-bit; another sample rate than `rate` is a warning, or an error with
    strict_rate) or of any other file as raw s16le."""
    p = pathlib.Path(path)
    if p.suffix.lower() != ".wav":
        return unpack(p.read_bytes())
    with wave.open(str(p)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "need mono 16-bit"
        if w.getframerate() != rate:
            assert not strict_rate, f"sample rate {w.getframerate()} Hz, expected {rate}"
            print(f"warning: sample rate {w.getframerate()} Hz, expected {rate}", file=sys.stderr)
        return unpack(w.readframes(w.getnframes()))


def write_wav(path, samples, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pack(samples))
