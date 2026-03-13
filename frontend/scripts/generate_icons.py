"""Generate PWA icons from scratch using pure Python — no external dependencies needed."""
import os
import struct
import zlib


def create_icon_png(size: int, output_path: str) -> None:
    """Create a PNG icon with neon green N on dark background with rounded corners."""
    pixels = []
    corner_r = size * 0.21

    margin = size * 0.20
    stroke = size * 0.14

    nx1 = margin
    nx2 = margin + stroke
    nx3 = size - margin - stroke
    nx4 = size - margin
    ny1 = margin
    ny2 = size - margin

    for y in range(size):
        row = []
        for x in range(size):
            # Default: dark background
            r, g, b, a = 10, 10, 11, 255

            # Rounded corners: compute transparency in corner zones
            corner_transparent = False
            for (cx, cy) in [
                (corner_r, corner_r),
                (size - corner_r, corner_r),
                (corner_r, size - corner_r),
                (size - corner_r, size - corner_r),
            ]:
                if x < corner_r or x > size - corner_r:
                    if y < corner_r or y > size - corner_r:
                        dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                        if dist > corner_r:
                            corner_transparent = True
                            break

            if corner_transparent:
                row.extend([0, 0, 0, 0])
                continue

            # Detect "N" letter geometry
            in_letter = False

            # Left vertical bar
            if nx1 <= x <= nx2 and ny1 <= y <= ny2:
                in_letter = True
            # Right vertical bar
            elif nx3 <= x <= nx4 and ny1 <= y <= ny2:
                in_letter = True
            # Diagonal stroke from top-left bar to bottom-right bar
            elif nx2 < x < nx3:
                progress = (x - nx2) / (nx3 - nx2)
                diag_y = ny1 + progress * (ny2 - ny1)
                half_width = stroke * 0.65
                if abs(y - diag_y) < half_width:
                    in_letter = True

            if in_letter:
                r, g, b = 0, 255, 136  # #00ff88 neon green

            row.extend([r, g, b, a])

        # PNG filter type 0 (None) prefix byte
        pixels.append(bytes([0] + row))

    raw = b"".join(pixels)

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        payload = chunk_type + data
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", crc)

    # IHDR: width, height, bit depth=8, color type=6 (RGBA), compression=0, filter=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)

    png_bytes = b"\x89PNG\r\n\x1a\n"
    png_bytes += chunk(b"IHDR", ihdr_data)
    png_bytes += chunk(b"IDAT", zlib.compress(raw, level=9))
    png_bytes += chunk(b"IEND", b"")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as fh:
        fh.write(png_bytes)

    print(f"  Created {output_path} ({size}x{size})")


if __name__ == "__main__":
    sizes = [72, 96, 128, 144, 152, 180, 192, 384, 512]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pwa_dir = os.path.join(script_dir, "..", "public", "pwa")

    print("Generating PWA icons...")
    for s in sizes:
        create_icon_png(s, os.path.join(pwa_dir, f"icon-{s}.png"))

    print(f"Done. {len(sizes)} icons written to frontend/public/pwa/")
