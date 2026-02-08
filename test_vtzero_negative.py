#!/usr/bin/env python3
"""
Test if vtzero (C++ library used by tilemaker) accepts negative coordinates
"""
import gzip
import struct

# Test: create a simple vector tile manually with vtzero-like format
# For now, let's just decode the existing tile and check if it has negative coords

import sqlite3

def check_tile_for_negative_coords(mbtiles_path, zoom, x, y):
    """Check if tile has features with negative X coordinates"""
    conn = sqlite3.connect(mbtiles_path)
    cursor = conn.cursor()

    max_coord = (1 << zoom) - 1
    flip_y = max_coord - y

    cursor.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (zoom, x, flip_y)
    )

    row = cursor.fetchone()
    if not row:
        print("Tile not found")
        return

    tile_data = row[0]
    if tile_data[:2] == b'\x1f\x8b':
        tile_data = gzip.decompress(tile_data)

    print(f"Tile {zoom}/{x}/{y} size: {len(tile_data)} bytes")

    # Check for MVT magic bytes
    if tile_data[:0] != b'\x1a':  # Not a valid MVT
        print("Not a valid MVT tile (wrong magic)")
        return

    # Simple check: scan for negative coordinate indicators
    # In MVT, coordinates are encoded as Protobuf varints
    # This is a simplified check - we'd need proper Protobuf decoding

    # Instead, use mapbox_vector_tile which we know works
    try:
        from mapbox_vector_tile import decode
        tile = decode(tile_data)

        has_negative = False
        for layer_name, layer_data in tile.items():
            if not isinstance(layer_data, dict):
                continue
            features = layer_data.get('features', [])
            for feat in features:
                if isinstance(feat, dict):
                    geom = feat.get('geometry', {})
                    if geom.get('type') == 'Point':
                        coords = geom.get('coordinates')
                        if coords and len(coords) >= 2:
                            if coords[0] < 0:
                                has_negative = True
                                print(f"  Found negative X in layer '{layer_name}': {feat.get('id')} coords={coords}")
                            if coords[1] < 0:
                                has_negative = True
                                print(f"  Found negative Y in layer '{layer_name}': {feat.get('id')} coords={coords}")

        if has_negative:
            print("\n✓ Tile has features with negative coordinates!")
        else:
            print("\n✗ No features with negative coordinates found")

    except Exception as e:
        print(f"Error decoding: {e}")

if __name__ == "__main__":
    import sys
    check_tile_for_negative_coords("/mnt/cache/data.mbtiles", 14, 9544, 4740)
