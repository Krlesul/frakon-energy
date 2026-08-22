#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/branding/source"
OUT="$ROOT/branding/generated"
HA="$ROOT/custom_components/frakon_energy/brand"

rm -rf "$OUT"
mkdir -p "$OUT" "$HA"

render_brand() {
  local brand="$1"
  local variant="$2"
  local src_dir="$SRC/$brand"
  local out_dir="$OUT/$brand/$variant"
  mkdir -p "$out_dir"

  rsvg-convert -w 512 -h 512 "$src_dir/icon_${variant}.svg" -o "$out_dir/icon.png"
  rsvg-convert -w 1024 -h 1024 "$src_dir/icon_${variant}.svg" -o "$out_dir/icon@2x.png"
  rsvg-convert -w 1200 -h 320 "$src_dir/logo_${variant}.svg" -o "$out_dir/logo.png"
  rsvg-convert -w 2400 -h 640 "$src_dir/logo_${variant}.svg" -o "$out_dir/logo@2x.png"
  cp "$src_dir/icon_${variant}.svg" "$out_dir/icon.svg"
  cp "$src_dir/logo_${variant}.svg" "$out_dir/logo.svg"

  convert "$out_dir/icon@2x.png" -resize 1024x1024 "$out_dir/github_avatar.png"
  cp "$out_dir/icon@2x.png" "$out_dir/app_icon_1024.png"
  convert "$out_dir/icon.png" -define icon:auto-resize=256,128,64,48,32,16 "$out_dir/favicon.ico"

  for size in 16 32 48 64 128 180 192 256 512; do
    convert "$out_dir/icon.png" -resize "${size}x${size}" "$out_dir/favicon-${size}x${size}.png"
  done
}

for brand in frakon_os frakon_energy; do
  for variant in dark light; do
    render_brand "$brand" "$variant"
  done
done

# Home Assistant custom integration brand files.
cp "$OUT/frakon_energy/dark/icon.png" "$HA/icon.png"
cp "$OUT/frakon_energy/dark/icon@2x.png" "$HA/icon@2x.png"
cp "$OUT/frakon_energy/light/icon.png" "$HA/dark_icon.png"
cp "$OUT/frakon_energy/light/icon@2x.png" "$HA/dark_icon@2x.png"
cp "$OUT/frakon_energy/dark/logo.png" "$HA/logo.png"
cp "$OUT/frakon_energy/dark/logo@2x.png" "$HA/logo@2x.png"
cp "$OUT/frakon_energy/light/logo.png" "$HA/dark_logo.png"
cp "$OUT/frakon_energy/light/logo@2x.png" "$HA/dark_logo@2x.png"

echo "Brand assets generated in $OUT and $HA"
