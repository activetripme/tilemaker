#!/usr/bin/env python3
"""
Read tile directly from mbtiles database (bypassing HTTP server)
Visualize vector tile points with target points highlighted
Can search across all layers and neighboring tiles
"""
import sqlite3
import gzip
import sys
import argparse
import os
from collections import defaultdict

# Try to import visualization libraries
try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("Warning: matplotlib not found. Install with: pip install matplotlib")

try:
    from mapbox_vector_tile import decode
    HAS_MVT = True
except ImportError:
    HAS_MVT = False
    print("Warning: mapbox-vector-tile not found. Install with: pip install mapbox-vector-tile")

def read_tile_from_mbtiles(mbtiles_path, zoom, x, y, verbose=True):
    """Read and decode a tile directly from mbtiles"""
    conn = sqlite3.connect(mbtiles_path)
    cursor = conn.cursor()

    # TMS flip for y coordinate
    max_coord = (1 << zoom) - 1
    flip_y = max_coord - y

    if verbose:
        print(f"Reading tile {zoom}/{x}/{y} (flipped Y: {flip_y}) from mbtiles...")

    cursor.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (zoom, x, flip_y)
    )

    row = cursor.fetchone()
    if not row:
        if verbose:
            print(f"  Tile not found in mbtiles")
        conn.close()
        return None

    tile_data = row[0]

    # Check if gzipped
    if tile_data[:2] == b'\x1f\x8b':
        if verbose:
            print(f"  Tile size (compressed): {len(tile_data)} bytes")
        tile_data = gzip.decompress(tile_data)
        if verbose:
            print(f"  Tile size (decompressed): {len(tile_data)} bytes")
    else:
        if verbose:
            print(f"  Tile size: {len(tile_data)} bytes (not compressed)")

    conn.close()
    return tile_data

def find_targets_in_tile(tile_data, target_ids, layer_name=None):
    """Find all target features in a tile.
    If layer_name is None, search all layers.
    Returns dict: {layer_name: [(feat, coords), ...]}
    """
    if not HAS_MVT:
        return {}

    try:
        tile = decode(tile_data)
    except Exception as e:
        print(f"  ERROR decoding tile: {e}")
        return {}

    results = defaultdict(list)

    layers_to_search = [layer_name] if layer_name else tile.keys()

    for ln in layers_to_search:
        if ln not in tile:
            continue
        layer = tile[ln]
        features = layer.get('features', [])

        for feat in features:
            if not isinstance(feat, dict):
                continue
            feat_id = feat.get('id')
            if feat_id in target_ids:
                geom = feat.get('geometry', {})
                if geom.get('type') == 'Point':
                    coords = geom.get('coordinates')
                    if coords and len(coords) >= 2:
                        results[ln].append((feat, coords))

    return dict(results)

def get_neighbor_tiles(zoom, x, y):
    """Get coordinates of neighboring tiles (8 neighbors + center)"""
    tiles = []
    for dy in [-1, 0, 1]:
        for dx in [-1, 0, 1]:
            nx, ny = x + dx, y + dy
            max_coord = (1 << zoom) - 1
            if 0 <= nx <= max_coord and 0 <= ny <= max_coord:
                tiles.append((zoom, nx, ny))
    return tiles

def search_all_tiles(mbtiles_path, tiles, target_ids, layer_name=None, verbose=True):
    """Search for target IDs across multiple tiles.
    Returns dict: {(z, x, y): {layer_name: [(feat, coords), ...]}}
    """
    all_results = {}

    for z, x, y in tiles:
        if verbose and (x != tiles[0][1] or y != tiles[0][2]):
            print()  # Blank line between tiles
        tile_data = read_tile_from_mbtiles(mbtiles_path, z, x, y, verbose=verbose)
        if tile_data:
            results = find_targets_in_tile(tile_data, target_ids, layer_name)
            if results:
                all_results[(z, x, y)] = results

    return all_results

def print_results(all_results, target_ids):
    """Print search results in a readable format"""
    if not all_results:
        print(f"\nNo target IDs found: {target_ids}")
        return

    print(f"\n{'='*60}")
    print(f"SEARCH RESULTS for target IDs: {target_ids}")
    print(f"{'='*60}")

    total_found = 0
    for tile_coords, layer_results in all_results.items():
        z, x, y = tile_coords
        print(f"\nTile {z}/{x}/{y}:")
        for layer_name, features_list in layer_results.items():
            print(f"  Layer '{layer_name}': {len(features_list)} feature(s)")
            for feat, coords in features_list:
                props = feat.get('properties', {})
                name = props.get('name', 'N/A')
                feat_id = feat.get('id')
                print(f"    ID={feat_id}, name={name}, coords=({coords[0]:.6f}, {coords[1]:.6f})")
                total_found += 1

    print(f"\n{'='*60}")
    print(f"Total features found: {total_found}")
    print(f"{'='*60}")

def visualize_tiles(all_results, target_ids, layer_name=None, output_path=None, mbtiles_path=None):
    """Visualize results from multiple tiles with ALL points shown and target points highlighted."""
    if not HAS_MATPLOTLIB:
        print("Cannot visualize: matplotlib not available")
        return False

    if not all_results:
        print("No results to visualize")
        return False

    # Collect all points: target and non-target
    all_x = []
    all_y = []
    target_points = []  # (x, y, id, name, layer, tile_info)
    other_points = []   # (x, y, layer, tile_info)

    # First, collect target points from results
    for tile_coords, layer_results in all_results.items():
        z, x, y = tile_coords
        tile_info = f"{z}/{x}/{y}"
        for ln, features_list in layer_results.items():
            if layer_name and ln != layer_name:
                continue
            for feat, coords in features_list:
                feat_id = feat.get('id')
                props = feat.get('properties', {})
                name = props.get('name', 'N/A')
                target_points.append((coords[0], coords[1], feat_id, name, ln, tile_info))
                all_x.append(coords[0])
                all_y.append(coords[1])

    if not target_points:
        print("No target points to visualize")
        return False

    # Now read tiles again to get ALL points for context
    for tile_coords in all_results.keys():
        z, x, y = tile_coords
        tile_info = f"{z}/{x}/{y}"

        # Read tile data
        if not mbtiles_path:
            continue
        tile_data = read_tile_from_mbtiles(mbtiles_path, z, x, y, verbose=False)
        if not tile_data or not HAS_MVT:
            continue

        try:
            tile = decode(tile_data)

            # Determine which layers to show
            if layer_name:
                layers_to_show = [layer_name] if layer_name in tile else []
            else:
                # Show layers that have target points
                layers_to_show = list(all_results[tile_coords].keys())

            for ln in layers_to_show:
                if ln not in tile:
                    continue
                layer = tile[ln]
                features = layer.get('features', [])

                for feat in features:
                    if not isinstance(feat, dict):
                        continue
                    geom = feat.get('geometry', {})
                    if geom.get('type') != 'Point':
                        continue
                    coords = geom.get('coordinates')
                    if not coords or len(coords) < 2:
                        continue

                    feat_id = feat.get('id')
                    px, py = coords[0], coords[1]
                    all_x.append(px)
                    all_y.append(py)

                    # Skip if it's a target point (already added)
                    is_target = False
                    for tp in target_points:
                        if (abs(tp[0] - px) < 1e-10 and abs(tp[1] - py) < 1e-10 and
                            tp[2] == feat_id and tp[4] == ln):
                            is_target = True
                            break

                    if not is_target:
                        other_points.append((px, py, ln, tile_info))

        except Exception as e:
            print(f"  ERROR reading tile for visualization: {e}")

    # Create visualization
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))

    # Plot all non-target points in gray (smaller, more transparent)
    if other_points:
        ox = [p[0] for p in other_points]
        oy = [p[1] for p in other_points]
        ax.scatter(ox, oy, c='lightgray', s=20, alpha=0.4, label=f'Other points ({len(other_points)})', zorder=1)

    # Plot target points in red (larger, on top)
    tx = [p[0] for p in target_points]
    ty = [p[1] for p in target_points]
    ax.scatter(tx, ty, c='red', s=200, alpha=0.9, edgecolors='darkred', linewidths=2,
              label=f'Target points ({len(target_points)})', zorder=10)

    # Add annotations for target points
    for idx, (x, y, fid, name, layer, tile_info) in enumerate(target_points):
        display_name = name if name != 'N/A' else ''

        label = f"ID: {fid}"
        if display_name:
            label += f"\n{display_name}"
        label += f"\nLayer: {layer}"
        label += f"\nTile: {tile_info}"

        # Offset annotations to avoid overlap
        offset_x = 15 + (idx % 3) * 30
        offset_y = 15 + (idx % 3) * 30

        ax.annotate(label, (x, y), xytext=(offset_x, offset_y), textcoords='offset points',
                   bbox=dict(boxstyle='round,pad=0.5', fc='yellow', alpha=0.8),
                   fontsize=7, arrowprops=dict(arrowstyle='->', color='red', lw=1.5))

    # Set bounds with some padding
    if all_x and all_y:
        x_margin = (max(all_x) - min(all_x)) * 0.1 or 0.001
        y_margin = (max(all_y) - min(all_y)) * 0.1 or 0.001
        ax.set_xlim(min(all_x) - x_margin, max(all_x) + x_margin)
        ax.set_ylim(min(all_y) - y_margin, max(all_y) + y_margin)

    ax.set_xlabel('Longitude', fontsize=11)
    ax.set_ylabel('Latitude', fontsize=11)
    ax.set_title(f'Vector Tile Visualization - All Points\nTarget IDs: {sorted(target_ids)}', fontsize=13)
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)
    ax.set_aspect('equal')

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Visualization saved to: {output_path}")
    else:
        plt.show()

    plt.close()
    return True

def main():
    parser = argparse.ArgumentParser(
        description='Read and visualize vector tiles from mbtiles database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check tile with target ID (text output)
  %(prog)s --tile 14 9579 4762 --target 1380548981

  # Visualize with highlighted target point
  %(prog)s --tile 14 9579 4762 --target 1380548981 --visualize --output tile_viz.png

  # Search across ALL layers
  %(prog)s --tile 14 9579 4762 --target 1380548981 --all-layers

  # Search in neighboring tiles too
  %(prog)s --tile 14 9579 4762 --target 1380548981 --neighbors --visualize

  # All layers + neighbors + visualize
  %(prog)s --tile 14 9579 4762 --target 1380548981 --all-layers --neighbors --visualize

  # Multiple target IDs
  %(prog)s --tile 14 9579 4762 --target 1380548981 1394850514 --all-layers --neighbors
        """
    )
    parser.add_argument('--mbtiles', default='/mnt/cache/data.mbtiles',
                       help='Path to mbtiles file (default: /mnt/cache/data.mbtiles)')
    parser.add_argument('--tile', nargs=3, type=int, metavar=('Z', 'X', 'Y'),
                       help='Tile coordinates (zoom x y)')
    parser.add_argument('--target', nargs='+', type=int, metavar='ID',
                       help='Target feature ID(s) to highlight')
    parser.add_argument('--layer', default=None,
                       help='Layer name to search (default: all layers if --all-layers, else poi)')
    parser.add_argument('--all-layers', action='store_true',
                       help='Search across all layers in the tile(s)')
    parser.add_argument('--neighbors', action='store_true',
                       help='Include neighboring 8 tiles in search')
    parser.add_argument('--visualize', action='store_true',
                       help='Create visualization plot')
    parser.add_argument('--output', metavar='PATH',
                       help='Output path for visualization image')
    parser.add_argument('--list-layers', action='store_true',
                       help='List all available layers in the tile')

    args = parser.parse_args()

    # Legacy support: positional ID argument
    if len(sys.argv) > 1 and not any(arg.startswith('-') for arg in sys.argv[1:]):
        args.target = [int(sys.argv[1])]
        args.tile = [14, 9579, 4762]
        args.visualize = False
        args.layer = 'poi'

    if not args.tile:
        parser.error("--tile argument is required (e.g., --tile 14 9579 4762)")

    zoom, x, y = args.tile
    target_ids = set(args.target) if args.target else set()

    if not target_ids:
        parser.error("--target argument is required (e.g., --target 1380548981)")

    # Determine layer to search
    layer_name = None if args.all_layers else (args.layer or 'poi')

    print(f"\n{'='*60}")
    print(f"Searching for target IDs: {target_ids}")
    print(f"{'='*60}")

    # Get tiles to search
    if args.neighbors:
        tiles = get_neighbor_tiles(zoom, x, y)
        print(f"\nSearching {len(tiles)} tiles (including neighbors)")
    else:
        tiles = [(zoom, x, y)]
        print(f"\nSearching tile {zoom}/{x}/{y}")

    if layer_name:
        print(f"Layer: {layer_name}")
    else:
        print(f"Layer: ALL layers")

    # Search
    all_results = search_all_tiles(args.mbtiles, tiles, target_ids, layer_name)

    # List layers if requested
    if args.list_layers:
        for z, tx, ty in tiles:
            print(f"\n{'='*40}")
            print(f"Layers in tile {z}/{tx}/{ty}:")
            print(f"{'='*40}")
            tile_data = read_tile_from_mbtiles(args.mbtiles, z, tx, ty, verbose=False)
            if tile_data and HAS_MVT:
                try:
                    tile = decode(tile_data)
                    for layer_name, layer_data in tile.items():
                        features = layer_data.get('features', [])
                        print(f"  '{layer_name}': {len(features)} features")
                        geom_types = {}
                        for feat in features:
                            if isinstance(feat, dict):
                                geom_type = feat.get('geometry', {}).get('type', 'Unknown')
                                geom_types[geom_type] = geom_types.get(geom_type, 0) + 1
                        for geom_type, count in sorted(geom_types.items()):
                            print(f"    {geom_type}: {count}")
                except Exception as e:
                    print(f"  ERROR: {e}")

    # Print results
    print_results(all_results, target_ids)

    # Visualize
    if args.visualize:
        print("\nCreating visualization...")
        visualize_tiles(all_results, target_ids, layer_name, args.output, args.mbtiles)

    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
