# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8"]
# ///
"""
Reconstruction 3D d'une surface à partir d'un export carto-pentes (.geojson).

Principe : la trace GPS donne la position ; le tangage (pente du sol dans le sens
de marche, repère châssis) donne la dérivée d'altitude le long du parcours. On
intègre donc l'altitude relative pas à pas :

    dz_i = ds_i * tan(tangage_i)          (ds = distance parcourue entre 2 points)
    z_i  = z_{i-1} + dz_i

L'altitude GPS n'est PAS utilisée (trop bruitée) ; on reconstruit le relief par
intégration des pentes, bien plus fin à l'échelle d'un jardin.

Sorties (dossier data/) :
  - <nom>_3d.geojson : points avec altitude reconstruite (z) en propriété
  - <nom>_profil.png : altitude le long du parcours + tangage
  - <nom>_plan.png   : vue en plan colorée par l'altitude reconstruite

Usage :
    uv run reconstruct3d.py "chemin/vers/trace.geojson"
    uv run reconstruct3d.py trace.geojson --invert-tangage   # si la pente est inversée
    uv run reconstruct3d.py trace.geojson --detrend          # retire le biais (test/pose non calée)
"""

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def load_samples(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ech = [f for f in data["features"] if f["properties"].get("type") == "echantillon"]
    ech.sort(key=lambda f: f["properties"].get("timestamp", ""))
    return ech


def to_local_meters(lon, lat):
    """Projection équirectangulaire locale (m) autour du centroïde."""
    lon0, lat0 = lon.mean(), lat.mean()
    x = (lon - lon0) * math.cos(math.radians(lat0)) * 111320.0
    y = (lat - lat0) * 110540.0
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--invert-tangage", action="store_true", help="inverse le signe du tangage")
    ap.add_argument("--detrend", action="store_true", help="retire le tangage médian (biais de pose)")
    ap.add_argument("--vexag", type=float, default=3.0, help="exagération verticale pour la vue 3D")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    ech = load_samples(src)
    if len(ech) < 2:
        print("Pas assez d'échantillons.")
        return

    lon = np.array([f["geometry"]["coordinates"][0] for f in ech])
    lat = np.array([f["geometry"]["coordinates"][1] for f in ech])
    tang = np.array([f["properties"].get("tangage_deg") or 0.0 for f in ech], float)
    roul = np.array([f["properties"].get("roulis_deg") or 0.0 for f in ech], float)
    acc = np.array([f["properties"].get("gps_acc_m") or float("nan") for f in ech], float)

    if args.invert_tangage:
        tang = -tang
    bias = 0.0
    if args.detrend:
        bias = float(np.median(tang))
        tang = tang - bias

    x, y = to_local_meters(lon, lat)

    # distances pas à pas et intégration de l'altitude
    dx = np.diff(x)
    dy = np.diff(y)
    ds = np.hypot(dx, dy)
    dz = ds * np.tan(np.radians(tang[1:]))
    z = np.concatenate([[0.0], np.cumsum(dz)])

    climb = float(dz[dz > 0].sum())
    descent = float(-dz[dz < 0].sum())
    dist = float(ds.sum())
    # dérive : écart entre départ et fin si le parcours revient près du départ
    close_d = math.hypot(x[-1] - x[0], y[-1] - y[0])

    print(f"Échantillons        : {len(ech)}")
    print(f"Distance parcourue  : {dist:.1f} m")
    print(f"Tangage médian      : {bias + np.median(tang):.1f}°  (biais retiré : {bias:.1f}°)" if args.detrend
          else f"Tangage médian      : {np.median(tang):.1f}°")
    print(f"Altitude reconstruite: min {z.min():.2f} m  max {z.max():.2f} m  amplitude {z.max()-z.min():.2f} m")
    print(f"Cumul montée/descente: +{climb:.1f} m / -{descent:.1f} m  (net {z[-1]:.1f} m)")
    print(f"Retour au depart     : {close_d:.1f} m  (derive verticale brute = {z[-1]:.1f} m si boucle fermee)")
    print(f"GPS précision moyenne: {np.nanmean(acc):.1f} m")

    outdir = Path(args.outdir) if args.outdir else src.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    # --- GeoJSON 3D ---
    feats = []
    lon0, lat0 = lon.mean(), lat.mean()
    for i in range(len(ech)):
        feats.append({
            "type": "Feature",
            "properties": {
                "z_rel_m": round(float(z[i]), 3),
                "tangage_deg": round(float(tang[i] + (bias if args.detrend else 0)), 2),
                "roulis_deg": round(float(roul[i]), 2),
            },
            "geometry": {"type": "Point", "coordinates": [float(lon[i]), float(lat[i]), round(float(z[i]), 3)]},
        })
    out_geo = outdir / f"{stem}_3d.geojson"
    out_geo.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    # --- Profil : altitude + tangage le long du parcours ---
    s = np.concatenate([[0.0], np.cumsum(ds)])
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    a1.plot(s, z, color="#2e7d32")
    a1.fill_between(s, z, z.min(), color="#2e7d32", alpha=.15)
    a1.set_ylabel("Altitude reconstruite (m)"); a1.grid(alpha=.3)
    a1.set_title(f"{stem} — profil ({dist:.0f} m)")
    a2.plot(s, tang + (bias if args.detrend else 0), color="#c62828")
    a2.axhline(0, color="#888", lw=.8)
    a2.set_ylabel("Tangage (°)"); a2.set_xlabel("Distance le long du parcours (m)"); a2.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_profil.png", dpi=110); plt.close(fig)

    # --- Plan coloré par altitude ---
    fig, ax = plt.subplots(figsize=(7, 7))
    sc = ax.scatter(x, y, c=z, cmap="terrain", s=18)
    ax.plot(x, y, color="#999", lw=.5, alpha=.6)
    ax.scatter([x[0]], [y[0]], c="k", marker="o", s=40, label="départ")
    ax.set_aspect("equal"); ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)")
    ax.set_title(f"{stem} — vue en plan (couleur = altitude)"); ax.legend()
    fig.colorbar(sc, label="Altitude reconstruite (m)")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_plan.png", dpi=110); plt.close(fig)

    # --- Surface 3D (triangulation de Delaunay sur les points parcourus) ---
    from matplotlib.tri import Triangulation
    tri = Triangulation(x, y)
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    surf = ax.plot_trisurf(tri, z, cmap="terrain", linewidth=0.15,
                           edgecolor=(0, 0, 0, 0.2), antialiased=True)
    xr, yr = x.max() - x.min(), y.max() - y.min()
    zr = max(z.max() - z.min(), 1e-3)
    ax.set_box_aspect((xr, yr, zr * args.vexag))
    ax.view_init(elev=35, azim=-60)
    ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)"); ax.set_zlabel("Alt (m)")
    ax.set_title(f"{stem} — relief 3D (exag. verticale ×{args.vexag:g})")
    fig.colorbar(surf, label="Altitude (m)", shrink=.6, pad=.1)
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_surface.png", dpi=120); plt.close(fig)

    # --- Courbes de niveau ---
    fig, ax = plt.subplots(figsize=(7, 7))
    cf = ax.tricontourf(tri, z, levels=14, cmap="terrain")
    cl = ax.tricontour(tri, z, levels=14, colors="k", linewidths=.4, alpha=.5)
    ax.clabel(cl, fmt="%.1f", fontsize=7)
    ax.plot(x, y, color="#333", lw=.4, alpha=.4)
    ax.set_aspect("equal"); ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)")
    ax.set_title(f"{stem} — courbes de niveau (m)")
    fig.colorbar(cf, label="Altitude (m)")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_contours.png", dpi=120); plt.close(fig)

    print(f"\nSorties écrites dans : {outdir}")
    for n in ("_3d.geojson", "_profil.png", "_plan.png", "_surface.png", "_contours.png"):
        print(f"  {stem}{n}")


if __name__ == "__main__":
    main()
