# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8", "certifi"]
# ///
"""
Recale la reconstruction carto-pentes sur l'altimétrie IGN (RGE ALTI).

Notre reconstruction donne le relief par intégration du tangage, mais à une
constante de pente près (biais de pose). L'IGN fournit l'altitude absolue le long
du parcours. On recale donc notre profil intégré sur l'IGN par régression linéaire
en distance (ce qui retire exactement un biais de tangage constant), puis on
compare le détail fin.

    z_corr(s) = z_raw(s) + a*s + b     (a, b ajustés pour coller à l'IGN)

API : Géoplateforme IGN (ouverte, sans clé)
  https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json

Usage :
    uv run recalage_ign.py "chemin/vers/trace.geojson"
"""

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

IGN_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"


def load_samples(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ech = [f for f in data["features"] if f["properties"].get("type") == "echantillon"]
    ech.sort(key=lambda f: f["properties"].get("timestamp", ""))
    return ech


def to_local_meters(lon, lat):
    lon0, lat0 = lon.mean(), lat.mean()
    x = (lon - lon0) * math.cos(math.radians(lat0)) * 111320.0
    y = (lat - lat0) * 110540.0
    return x, y


def fetch_ign(lon, lat, chunk=50):
    """Altitude IGN (RGE ALTI) pour des listes lon/lat. Retourne np.array (NaN si trou)."""
    zs = []
    for i in range(0, len(lon), chunk):
        lo = lon[i:i + chunk]
        la = lat[i:i + chunk]
        q = urllib.parse.urlencode({
            "lon": "|".join(f"{v:.6f}" for v in lo),
            "lat": "|".join(f"{v:.6f}" for v in la),
            "resource": "ign_rge_alti_wld",
            "delimiter": "|",
            "zonly": "false",
        })
        url = f"{IGN_URL}?{q}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    data = json.loads(r.read().decode("utf-8"))
                for e in data["elevations"]:
                    z = e.get("z")
                    zs.append(float(z) if z is not None and z > -1000 else float("nan"))
                break
            except Exception as ex:
                if attempt == 2:
                    print(f"  ! échec lot {i}: {ex}", file=sys.stderr)
                    zs.extend([float("nan")] * len(lo))
                else:
                    time.sleep(1.0)
        print(f"  IGN {min(i + chunk, len(lon))}/{len(lon)} points", file=sys.stderr)
    return np.array(zs, float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--invert-tangage", action="store_true")
    ap.add_argument("--vexag", type=float, default=4.0)
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    ech = load_samples(src)
    lon = np.array([f["geometry"]["coordinates"][0] for f in ech])
    lat = np.array([f["geometry"]["coordinates"][1] for f in ech])
    tang = np.array([f["properties"].get("tangage_deg") or 0.0 for f in ech], float)
    if args.invert_tangage:
        tang = -tang

    x, y = to_local_meters(lon, lat)
    ds = np.hypot(np.diff(x), np.diff(y))
    s = np.concatenate([[0.0], np.cumsum(ds)])

    # Notre profil brut (intégration du tangage, sans detrend)
    z_raw = np.concatenate([[0.0], np.cumsum(ds * np.tan(np.radians(tang[1:])))])

    print("Interrogation IGN RGE ALTI…", file=sys.stderr)
    z_ign = fetch_ign(lon, lat)
    ok = np.isfinite(z_ign)
    if ok.sum() < 5:
        print("Pas assez de points IGN valides.")
        return

    # Recalage : z_ign ≈ z_raw + a*s + b  (moindres carrés sur les points valides)
    A = np.column_stack([s[ok], np.ones(ok.sum())])
    (a, b), *_ = np.linalg.lstsq(A, (z_ign[ok] - z_raw[ok]), rcond=None)
    z_corr = z_raw + a * s + b

    resid = z_corr[ok] - z_ign[ok]
    rms = float(np.sqrt(np.mean(resid ** 2)))
    bias_deg = math.degrees(math.atan(a))  # pente moyenne récupérée via le recalage

    # Corrélation pente capteur vs pente IGN (validation du capteur)
    slope_sensor = np.tan(np.radians(tang[1:]))
    dz_ign = np.diff(z_ign)
    m = np.isfinite(dz_ign) & (ds > 0.3)
    slope_ign = np.where(ds > 0, dz_ign / np.where(ds == 0, 1, ds), np.nan)
    corr = float(np.corrcoef(slope_sensor[m], slope_ign[m])[0, 1]) if m.sum() > 3 else float("nan")

    print(f"Points IGN valides   : {ok.sum()}/{len(ech)}")
    print(f"Altitude IGN         : {np.nanmin(z_ign):.2f} → {np.nanmax(z_ign):.2f} m  (amplitude {np.nanmax(z_ign)-np.nanmin(z_ign):.2f} m)")
    print(f"Pente moy. récupérée : {bias_deg:+.2f}°  (biais de tangage estimé : {-bias_deg:+.2f}°)")
    print(f"Résidu capteur↔IGN   : RMS {rms:.2f} m  (écart du détail fin après recalage)")
    print(f"Corrélation pentes   : r = {corr:+.2f}  (capteur vs IGN, par pas)")

    outdir = Path(args.outdir) if args.outdir else src.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    # GeoJSON 3D recalé (z = altitude absolue recalée IGN)
    feats = [{
        "type": "Feature",
        "properties": {"z_m": round(float(z_corr[i]), 3), "z_ign_m": (None if not np.isfinite(z_ign[i]) else round(float(z_ign[i]), 2))},
        "geometry": {"type": "Point", "coordinates": [float(lon[i]), float(lat[i]), round(float(z_corr[i]), 3)]},
    } for i in range(len(ech))]
    (outdir / f"{stem}_recale_ign.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    # Profil comparé
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(s, z_ign, color="#1565c0", lw=2, label="IGN RGE ALTI")
    ax.plot(s, z_corr, color="#c62828", lw=1.4, label="carto-pentes (recalé)")
    ax.plot(s, z_raw + b, color="#aaa", lw=1, ls="--", label="carto-pentes brut (sans recalage)")
    ax.set_xlabel("Distance le long du parcours (m)"); ax.set_ylabel("Altitude (m)")
    ax.set_title(f"{stem} — profil capteur recalé sur IGN  (RMS {rms:.2f} m, r={corr:+.2f})")
    ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_profil_ign.png", dpi=120); plt.close(fig)

    # Surface 3D recalée + IGN en transparence
    from matplotlib.tri import Triangulation
    tri = Triangulation(x, y)
    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_trisurf(tri, z_corr, cmap="terrain", linewidth=0.1, edgecolor=(0, 0, 0, .15), antialiased=True)
    zi = np.where(np.isfinite(z_ign), z_ign, np.nanmean(z_ign))
    ax.plot_trisurf(tri, zi, color=(0.1, 0.4, 0.8, 0.18), linewidth=0)
    xr, yr = x.max() - x.min(), y.max() - y.min()
    zr = max(np.nanmax([z_corr.max(), np.nanmax(z_ign)]) - np.nanmin([z_corr.min(), np.nanmin(z_ign)]), 1e-3)
    ax.set_box_aspect((xr, yr, zr * args.vexag))
    ax.view_init(elev=32, azim=-60)
    ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)"); ax.set_zlabel("Alt (m)")
    ax.set_title(f"{stem} — relief recalé IGN (rouge=capteur, bleu=IGN, exag.×{args.vexag:g})")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_surface_ign.png", dpi=120); plt.close(fig)

    print(f"\nSorties dans : {outdir}")
    for n in ("_recale_ign.geojson", "_profil_ign.png", "_surface_ign.png"):
        print(f"  {stem}{n}")


if __name__ == "__main__":
    main()
