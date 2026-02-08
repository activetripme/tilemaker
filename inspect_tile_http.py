#!/usr/bin/env python3
"""
Inspect vector tile from HTTP endpoint - Fixed version
Usage: python3 inspect_tile_http.py
"""

import requests
import gzip
import json
import sys

try:
    from mapbox_vector_tile import decode
except ImportError:
    print("Installing mapbox-vector-tile...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "mapbox-vector-tile"])
    from mapbox_vector_tile import decode


def inspect_tile(url, target_id=None, search_name=None):
    """Download and inspect vector tile from HTTP endpoint"""
    print(f"Fetching: {url}")

    try:
        response = requests.get(url)
        response.raise_for_status()

        # Check content type
        content_type = response.headers.get('Content-Type', '')
        print(f"Content-Type: {content_type}")
        print(f"Size: {len(response.content)} bytes")

        # Try to decompress if gzipped
        tile_data = response.content
        if tile_data[:2] == b'\x1f\x8b':  # gzip magic number
            print("Detected gzip compression, decompressing...")
            tile_data = gzip.decompress(tile_data)
            print(f"Decompressed size: {len(tile_data)} bytes")

        # Decode vector tile
        print("\n" + "="*60)
        print("DECODING VECTOR TILE")
        print("="*60)

        try:
            tile = decode(tile_data)

            if not tile:
                print("ERROR: Tile is empty or could not be decoded")
                return

            print(f"\nLayers found: {list(tile.keys())}")

            # Search for target object in all layers
            found_target = False
            all_place_features = []

            for layer_name, layer_data in tile.items():
                # layer_data is a dict with 'features' key
                if not isinstance(layer_data, dict):
                    print(f"\nWARNING: Layer '{layer_name}' has unexpected structure: {type(layer_data)}")
                    continue

                features = layer_data.get('features', [])
                extent = layer_data.get('extent', 4096)

                print(f"\n{'='*60}")
                print(f"LAYER: {layer_name} (extent={extent})")
                print(f"{'='*60}")
                print(f"Features count: {len(features)}")

                for i, feat in enumerate(features):
                    if not isinstance(feat, dict):
                        continue

                    feat_id = feat.get('id')
                    feat_type = feat.get('type', 'unknown')
                    properties = feat.get('properties', {})
                    geometry = feat.get('geometry', {})

                    # Collect place features for later analysis
                    if layer_name == 'place':
                        all_place_features.append({
                            'id': feat_id,
                            'name': properties.get('name'),
                            'class': properties.get('class'),
                            'coords': geometry.get('coordinates') if geometry.get('type') == 'Point' else None
                        })

                    # Check if this is our target by ID
                    is_target = target_id and str(feat_id) == str(target_id)

                    # Check if this is our target by name
                    is_target_by_name = search_name and search_name.lower() in str(properties.get('name', '')).lower()

                    if is_target or is_target_by_name:
                        found_target = True
                        print(f"\n  *** TARGET FOUND (ID: {feat_id}) ***")
                    else:
                        # Only print first few features to avoid spam
                        if i < 3 or (is_target or is_target_by_name):
                            print(f"\n  Feature #{i+1} (ID: {feat_id})")
                        else:
                            continue

                    print(f"    Type: {feat_type}")
                    print(f"    Properties:")
                    for k, v in properties.items():
                        print(f"      {k}: {v}")

                    if geometry:
                        geom_type = geometry.get('type')
                        print(f"    Geometry type: {geom_type}")
                        if geom_type == 'Point':
                            coords = geometry.get('coordinates')
                            print(f"    Coordinates (tile space {extent}x{extent}): {coords}")
                            # Calculate position in percentage
                            if coords:
                                x_pct = (coords[0] / extent) * 100
                                y_pct = (coords[1] / extent) * 100
                                print(f"    Position: {x_pct:.1f}% from left, {y_pct:.1f}% from top")

                # Check if layer is empty
                if len(features) == 0:
                    print("  (empty layer)")

            # Print all place features at the end
            if all_place_features:
                print(f"\n{'='*60}")
                print(f"ALL PLACE FEATURES IN TILE:")
                print(f"{'='*60}")
                for pf in all_place_features:
                    print(f"  ID: {pf['id']}, Name: {pf['name']}, Class: {pf['class']}, Coords: {pf['coords']}")

            if target_id and not found_target:
                print(f"\n{'='*60}")
                print(f"WARNING: Target object ID {target_id} NOT FOUND in this tile!")
                print(f"{'='*60}")
            elif target_id and found_target:
                print(f"\n{'='*60}")
                print(f"SUCCESS: Target object ID {target_id} FOUND!")
                print(f"{'='*60}")

        except Exception as e:
            print(f"ERROR decoding tile: {e}")
            import traceback
            traceback.print_exc()

    except requests.exceptions.RequestException as e:
        print(f"ERROR fetching tile: {e}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


def compare_tiles(urls, target_id=None, search_name=None):
    """Compare two tiles side by side"""
    print("\n" + "="*80)
    print("COMPARING TWO TILES")
    print("="*80)

    results = {}

    for url in urls:
        print(f"\nFetching: {url}")
        try:
            response = requests.get(url)
            response.raise_for_status()

            tile_data = response.content
            if tile_data[:2] == b'\x1f\x8b':
                tile_data = gzip.decompress(tile_data)

            tile = decode(tile_data)
            results[url] = tile

        except Exception as e:
            print(f"ERROR: {e}")
            results[url] = None

    # Compare results
    print("\n" + "="*80)
    print("COMPARISON RESULTS")
    print("="*80)

    for url, tile in results.items():
        if tile is None:
            print(f"\n{url}: FAILED")
            continue

        print(f"\n{url}:")
        print(f"  Layers: {list(tile.keys())}")

        # Collect all place features
        place_features = []
        for layer_name, layer_data in tile.items():
            if layer_name == 'place' and isinstance(layer_data, dict):
                features = layer_data.get('features', [])
                for feat in features:
                    if isinstance(feat, dict):
                        place_features.append({
                            'id': feat.get('id'),
                            'name': feat.get('properties', {}).get('name'),
                            'class': feat.get('properties', {}).get('class'),
                            'coords': feat.get('geometry', {}).get('coordinates')
                        })

        print(f"  Place features: {len(place_features)}")
        for pf in place_features:
            print(f"    - ID: {pf['id']}, Name: {pf['name']}, Class: {pf['class']}, Coords: {pf['coords']}")

        # Search for target
        found = False
        if target_id:
            for layer_name, layer_data in tile.items():
                if isinstance(layer_data, dict):
                    features = layer_data.get('features', [])
                    for feat in features:
                        if isinstance(feat, dict) and str(feat.get('id')) == str(target_id):
                            found = True
                            print(f"  ✓ Target object ID {target_id} FOUND in layer '{layer_name}'")
                            props = feat.get('properties', {})
                            print(f"    Properties: {props}")
                            geom = feat.get('geometry', {})
                            if geom.get('type') == 'Point':
                                print(f"    Coordinates: {geom.get('coordinates')}")

        if search_name:
            for layer_name, layer_data in tile.items():
                if isinstance(layer_data, dict):
                    features = layer_data.get('features', [])
                    for feat in features:
                        if isinstance(feat, dict):
                            name = feat.get('properties', {}).get('name', '')
                            if search_name.lower() in name.lower():
                                found = True
                                print(f"  ✓ Object with name '{search_name}' FOUND in layer '{layer_name}'")
                                print(f"    Full name: {name}")
                                print(f"    ID: {feat.get('id')}")

        if not found and (target_id or search_name):
            what = f"ID {target_id}" if target_id else f"name '{search_name}'"
            print(f"  ✗ Target object {what} NOT FOUND")


if __name__ == "__main__":
    import sys

    # Target object ID to search for
    TARGET_ID = 241466788

    # Optional: search by name (useful if ID doesn't match)
    SEARCH_NAME = None  # e.g., "Moscow" or None

    # Parse command line args
    if len(sys.argv) > 1:
        SEARCH_NAME = sys.argv[1]

    # Tile server base URL
    BASE_URL = "http://localhost:3002/cont"

    # Zoom and coordinates
    Z = 14
    tiles = [
        (9543, 4740),
        (9544, 4740)
    ]

    # Compare both tiles
    urls = [f"{BASE_URL}/{Z}/{x}/{y}" for x, y in tiles]
    compare_tiles(urls, TARGET_ID, SEARCH_NAME)

    # Also inspect each tile in detail
    print("\n" + "="*80)
    print("DETAILED INSPECTION")
    print("="*80)

    for x, y in tiles:
        url = f"{BASE_URL}/{Z}/{x}/{y}"
        print(f"\n\nInspecting tile {Z}/{x}/{y}")
        inspect_tile(url, TARGET_ID, SEARCH_NAME)
