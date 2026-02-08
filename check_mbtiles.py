#!/usr/bin/env python3
"""
Read tile directly from mbtiles database (bypassing HTTP server)
"""
import sqlite3
import gzip
import sys

def read_tile_from_mbtiles(mbtiles_path, zoom, x, y):
    """Read and decode a tile directly from mbtiles"""
    conn = sqlite3.connect(mbtiles_path)
    cursor = conn.cursor()

    # TMS flip for y coordinate
    # mbtiles uses TMS, but tilemaker uses XYZ
    # For z14, flip_y = (2^zoom - 1) - y
    max_coord = (1 << zoom) - 1
    flip_y = max_coord - y

    print(f"Reading tile {zoom}/{x}/{y} (flipped Y: {flip_y}) from mbtiles...")

    cursor.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (zoom, x, flip_y)
    )

    row = cursor.fetchone()
    if not row:
        print("ERROR: Tile not found in mbtiles!")
        return None

    tile_data = row[0]

    # Check if gzipped
    if tile_data[:2] == b'\x1f\x8b':
        print(f"Tile size (compressed): {len(tile_data)} bytes")
        tile_data = gzip.decompress(tile_data)
        print(f"Tile size (decompressed): {len(tile_data)} bytes")
    else:
        print(f"Tile size: {len(tile_data)} bytes (not compressed)")

    conn.close()
    return tile_data

def main():
    mbtiles_path = "/mnt/cache/data.mbtiles"

    # Check both tiles
    for x, y in [(9579, 4762), (9580, 4762)]:
        print(f"\n{'='*60}")
        print(f"Checking tile {14}/{x}/{y}")
        print(f"{'='*60}")

        tile_data = read_tile_from_mbtiles(mbtiles_path, 14, x, y)

        ID =int(sys.argv[1])

        if tile_data:
            try:
                from mapbox_vector_tile import decode
                tile = decode(tile_data)

                # Check POI layer
                if 'poi' in tile:
                    poi_layer = tile['poi']
                    features = poi_layer.get('features', [])
                    print(f"POI layer: {len(features)} features")

                    # Look for our target object
                    # 1394850514
                    # ./check_mbtiles.py 1380548981
                    print("look for " + str(ID))
                    target_found = False
                    for feat in features:
                        if isinstance(feat, dict) and feat.get('id') == ID:
                            target_found = True
                            print(f"\n  *** TARGET FOUND ***")
                            print(f"  ID: {feat.get('id')}")
                            print(f"  Properties: {feat.get('properties')}")
                            geom = feat.get('geometry', {})
                            print(f"  Geometry: {geom}")
                            if geom.get('type') == 'Point':
                                coords = geom.get('coordinates')
                                print(f"  Coordinates: {coords}")
                                if coords:
                                    print(f"  X={coords[0]}, Y={coords[1]}")

                    if not target_found:
                        print(f"\n  Target object ID NOT FOUND in POI layer")
                        print(f"\n  All POI features:")
                        for feat in features[:10]:  # First 10
                            if isinstance(feat, dict):
                                print(f"    - ID: {feat.get('id')}, Name: {feat.get('properties', {}).get('name', 'N/A')}")
                else:
                    print("POI layer: NOT FOUND in tile")

                # Check housenumber layer for comparison
                if 'housenumber' in tile:
                    hn_layer = tile['housenumber']
                    features = hn_layer.get('features', [])
                    print(f"\nHousenumber layer: {len(features)} features")

                    for feat in features:
                        if isinstance(feat, dict) and feat.get('id') == ID:
                            print(f"  Target found in housenumber: coords={feat.get('geometry', {}).get('coordinates')}")

            except Exception as e:
                print(f"ERROR decoding tile: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    main()
