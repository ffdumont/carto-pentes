# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8"]
# ///
"""
Surface SCHÉMATIQUE (théorique) du relief, calée sur le polygone KML de référence.

Modèle idéalisé pour communiquer la forme — pas une reconstruction point par point :
une coupe transversale à l'axe long du polygone, avec un PLATEAU central et deux
talus dissymétriques (NE marqué / SW plus doux), dont les pentes reprennent les
MOYENNES mesurées de part et d'autre de la crête. Constant le long de l'axe long.

Rendu épuré : aplats de couleur (pente %) + courbes de niveau (relief). Pas de points.

Sorties :
  <stem>_schema_topo.png : aplats pente % + courbes de niveau + emprise polygone
  <stem>_schema3d.png    : surface 3D schématique

Usage :
  uv run reconstruct_schema.py "data/...snapshot....geojson" --polygone data/cre-talus.kmz
  uv run reconstruct_schema.py ... --pente-ne 45 --pente-sw 22 --plateau 1.2
"""

import argparse
import json
import math
import re
import zipfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
import numpy as np


def read_polygon(path):
    p = Path(path)
    if p.suffix.lower() == ".kmz":
        with zipfile.ZipFile(p) as z:
            kml = z.read(next(n for n in z.namelist() if n.lower().endswith(".kml"))).decode("utf-8")
    else:
        kml = p.read_text(encoding="utf-8")
    pts = []
    for tok in re.search(r"<coordinates>(.*?)</coordinates>", kml, re.S).group(1).split():
        a = tok.split(",")
        if len(a) >= 2:
            pts.append((float(a[0]), float(a[1])))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--polygone", required=True)
    ap.add_argument("--pente-ne", type=float, default=60.0, help="pente du talus NE (long cote) en %%")
    ap.add_argument("--pente-sw", type=float, default=45.0, help="pente du talus SW (long cote) en %%")
    ap.add_argument("--larg-ne", type=float, default=0.6, help="largeur du talus NE (m)")
    ap.add_argument("--larg-sw", type=float, default=2.0, help="largeur du talus SW (m)")
    ap.add_argument("--pente-ew", type=float, default=25.0, help="pente des limites E/O (petits cotes) en %%")
    ap.add_argument("--larg-ew", type=float, default=1.0, help="largeur des bandes E/O (m)")
    ap.add_argument("--pente-centre", type=float, default=20.0, help="pente douce centrale en %% (le long du grand cote, vers le NO)")
    ap.add_argument("--marge", type=float, default=0.0, help="marge autour du polygone (0 = emprise exacte du terrain)")
    ap.add_argument("--pas", type=float, default=0.2, help="pas grille (m)")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    data = json.loads(src.read_text(encoding="utf-8"))
    S = [f for f in data["features"] if f["properties"].get("type") == "snapshot"]
    lat = np.array([f["geometry"]["coordinates"][1] for f in S])
    lon = np.array([f["geometry"]["coordinates"][0] for f in S])
    pct = np.array([f["properties"]["pente_pct"] for f in S], float)

    poly_ll = np.array(read_polygon(args.polygone))
    # Repère métrique centré sur le CENTRE DU POLYGONE (« calé sur le polygone »)
    lat0 = poly_ll[:, 1].mean()
    lon0 = poly_ll[:, 0].mean()
    mlon = math.cos(math.radians(lat0)) * 111320.0
    mlat = 110540.0
    P = np.column_stack([(poly_ll[:, 0] - lon0) * mlon, (poly_ll[:, 1] - lat0) * mlat])
    M = np.column_stack([(lon - lon0) * mlon, (lat - lat0) * mlat])

    center = P.mean(0)
    # Grand côté du polygone = direction de la pente douce centrale (vers le NO)
    _C = np.cov((P - center).T); _w, _V = np.linalg.eigh(_C)
    e_long = _V[:, np.argmax(_w)]
    if e_long @ np.array([-1.0, 1.0]) < 0:           # oriente vers le NO (E<0, N>0)
        e_long = -e_long
    S_CENTRE = args.pente_centre / 100.0

    # ----- Modèle EN BANDES dans le repère du polygone :
    #   e_cross = perpendiculaire au grand côté, orienté vers le NE.
    e_cross = np.array([e_long[1], -e_long[0]])
    if e_cross @ np.array([1.0, 1.0]) < 0:            # vers le NE (E>0, N>0)
        e_cross = -e_cross
    s_ne, s_sw, s_ew = args.pente_ne / 100, args.pente_sw / 100, args.pente_ew / 100

    # Grille = emprise exacte du polygone (marge 0)
    pas = args.pas; mg = args.marge
    gxs = np.arange(P[:, 0].min() - mg, P[:, 0].max() + mg + pas, pas)
    gys = np.arange(P[:, 1].min() - mg, P[:, 1].max() + mg + pas, pas)
    GX, GY = np.meshgrid(gxs, gys)
    x0, x1, y0, y1 = gxs.min(), gxs.max(), gys.min(), gys.max()
    pts = np.column_stack([GX.ravel(), GY.ravel()])
    dpos = pts - center
    v = dpos @ e_cross            # en travers : <0 = SW, >0 = NE
    t = dpos @ e_long             # le long : croissant vers le NO
    Pc = P - center
    vmin, vmax = (Pc @ e_cross).min(), (Pc @ e_cross).max()
    tmin, tmax = (Pc @ e_long).min(), (Pc @ e_long).max()
    inside = MplPath(P).contains_points(pts)

    # Bandes (priorité talus NE > talus SW > limites E/O > coeur), réparties
    # UNIFORMÉMENT le long de chaque côté du polygone.
    ne_band = v > vmax - args.larg_ne
    sw_band = (v < vmin + args.larg_sw) & ~ne_band
    end_band = ((t > tmax - args.larg_ew) | (t < tmin + args.larg_ew)) & ~ne_band & ~sw_band

    # Surface : selle inclinée 20% vers le NO + talus surimposés aux bords
    z = -S_CENTRE * t
    z = np.where(ne_band, z - s_ne * (v - (vmax - args.larg_ne)), z)
    z = np.where(sw_band, z - s_sw * ((vmin + args.larg_sw) - v), z)
    dend = np.where(t > 0, t - (tmax - args.larg_ew), (tmin + args.larg_ew) - t)
    z = np.where(end_band, z - s_ew * np.maximum(dend, 0.0), z)
    z = np.where(inside, z, np.nan)
    Zg = (z - np.nanmin(z)).reshape(GX.shape)

    # Pente affichée (%) par bande
    SL = np.full(len(pts), S_CENTRE * 100.0)
    SL = np.where(end_band, args.pente_ew, SL)
    SL = np.where(sw_band, args.pente_sw, SL)
    SL = np.where(ne_band, args.pente_ne, SL)
    SLg = np.where(inside, SL, np.nan).reshape(GX.shape)
    amp = float(np.nanmax(Zg))
    print(f"Forme : selle inclinee {args.pente_centre:.0f}% vers le NO")
    print(f"Talus NE {args.pente_ne:.0f}%/{args.larg_ne} m (tout le grand cote) | "
          f"SW {args.pente_sw:.0f}%/{args.larg_sw} m | limites E/O {args.pente_ew:.0f}%")
    print(f"Amplitude schematique : {amp:.2f} m")

    outdir = Path(args.outdir) if args.outdir else src.parent
    stem = src.stem
    Pclose = np.vstack([P, P[0]])

    # ---- Topo schématique : aplats pente % + courbes de niveau (relief)
    fig, ax = plt.subplots(figsize=(9, 8))
    cf = ax.contourf(GX, GY, SLg, levels=np.linspace(0, 70, 15), cmap="RdYlGn_r",
                     vmin=0, vmax=70, extend="max")
    cl = ax.contour(GX, GY, Zg, levels=np.linspace(0, amp, 9), colors="k", linewidths=.6, alpha=.7)
    ax.clabel(cl, fmt="%.1f m", fontsize=7)
    ax.plot(Pclose[:, 0], Pclose[:, 1], "-", color="#1565c0", lw=2.2, label="polygone réf.")
    ax.set_aspect("equal"); ax.set_xlabel("Est (m) →"); ax.set_ylabel("Nord (m) ↑")
    ax.set_title(f"{stem}\nSurface schématique — selle {args.pente_centre:.0f}% NO · talus NE {args.pente_ne:.0f}% / SW {args.pente_sw:.0f}%")
    lbl = dict(transform=ax.transAxes, fontweight="bold", fontsize=13, color="#1565c0")
    ax.text(0.5, 1.005, "N", ha="center", va="bottom", **lbl)
    ax.text(0.5, -0.06, "S", ha="center", va="top", **lbl)
    ax.text(1.005, 0.5, "E", ha="left", va="center", **lbl)
    ax.text(-0.04, 0.5, "O", ha="right", va="center", **lbl)
    fig.colorbar(cf, label="Pente (%)", shrink=.8)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_schema_topo.png", dpi=140); plt.close(fig)

    # ---- 3D schématique (Nord en haut / Est à droite)
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_surface(GX, GY, Zg, cmap="terrain", linewidth=0, antialiased=True, alpha=.95)
    ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)"); ax.set_zlabel("Hauteur rel. (m)")
    ax.set_title(f"{stem}\nSurface schématique : selle {args.pente_centre:.0f}% NO + talus NE/SW — amplitude {amp:.2f} m")
    try:
        ax.set_box_aspect((x1 - x0, y1 - y0, max(amp, .5) * 3))
    except Exception:
        pass
    ax.view_init(elev=48, azim=-90)
    ax.text2D(0.5, 0.97, "↑ NORD", transform=ax.transAxes, ha="center", fontweight="bold")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_schema3d.png", dpi=140); plt.close(fig)

    print(f"\nSorties :")
    print(f"  {stem}_schema_topo.png")
    print(f"  {stem}_schema3d.png")


if __name__ == "__main__":
    main()
