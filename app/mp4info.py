"""Lengths of the video and sound tracks of an MP4 file, read from its header (no ffmpeg, nothing is decoded)."""
import struct


def _boxes(b, s, e):
    while s + 8 <= e:
        size, kind = struct.unpack(">I4s", b[s:s + 8])
        head = 8
        if size == 1:
            size = struct.unpack(">Q", b[s + 8:s + 16])[0]
            head = 16
        elif size == 0:
            size = e - s
        if size < head:
            return
        yield kind.decode("latin1"), s + head, min(s + size, e)
        s += size


def track_seconds(data):
    """{'vide': seconds, 'soun': seconds} of the tracks of an MP4/MOV file given as bytes; {} when it cannot be read."""
    out = {}
    try:
        for kind, s, e in _boxes(data, 0, len(data)):
            if kind != "moov":
                continue
            for k2, s2, e2 in _boxes(data, s, e):
                if k2 != "trak":
                    continue
                scale = dur = handler = None
                for k3, s3, e3 in _boxes(data, s2, e2):
                    if k3 != "mdia":
                        continue
                    for k4, s4, e4 in _boxes(data, s3, e3):
                        if k4 == "mdhd":
                            if data[s4] == 0:
                                scale, dur = struct.unpack(">II", data[s4 + 12:s4 + 20])
                            else:
                                scale, dur = struct.unpack(">IQ", data[s4 + 20:s4 + 32])
                        elif k4 == "hdlr":
                            handler = data[s4 + 8:s4 + 12].decode("latin1")
                if handler and scale and dur:
                    out.setdefault(handler, round(dur / scale, 4))
    except (struct.error, IndexError):
        return {}
    return out
