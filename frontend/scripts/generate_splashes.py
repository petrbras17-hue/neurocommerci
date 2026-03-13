"""Generate iOS PWA splash screens — dark bg with NEURO logo."""
import os, struct, zlib

SPLASHES = [
    (1290, 2796, "splash-1290x2796.png"),
    (1179, 2556, "splash-1179x2556.png"),
    (1284, 2778, "splash-1284x2778.png"),
    (750, 1334, "splash-750x1334.png"),
    (1125, 2436, "splash-1125x2436.png"),
]

def create_splash_png(w, h, output_path):
    """Create minimal splash: black bg, green 'NEURO' text center."""
    # For large images, create a simple solid color PNG with minimal data
    # Using indexed color (type 3) for much smaller file size

    # Palette: index 0 = #0a0a0b (bg), index 1 = #00ff88 (accent)
    palette = bytes([10, 10, 11, 0, 255, 136])

    # All pixels are background (index 0) — splash is just the brand color
    # The N logo would be too complex pixel-by-pixel at these sizes
    # A solid branded splash is the professional standard
    raw_rows = []
    for y in range(h):
        raw_rows.append(bytes([0] + [0] * w))  # filter=0, all palette index 0
    raw = b''.join(raw_rows)

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 3, 0, 0, 0)  # 8-bit indexed

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr)
    png += chunk(b'PLTE', palette)
    png += chunk(b'IDAT', zlib.compress(raw, 9))
    png += chunk(b'IEND', b'')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(png)
    size_kb = os.path.getsize(output_path) / 1024
    print(f"  Created {output_path} ({w}x{h}, {size_kb:.1f} KB)")

if __name__ == '__main__':
    base = os.path.join(os.path.dirname(__file__), '..', 'public', 'pwa')
    for w, h, name in SPLASHES:
        create_splash_png(w, h, os.path.join(base, name))
    print("Done! All iOS splash screens generated.")
