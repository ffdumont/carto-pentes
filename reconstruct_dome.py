# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "scipy>=1.11", "matplotlib>=3.8"]
# ///
"""
Reconstruction 3D LISSÉE du relief à partir des snapshots (pente + orientation).

Pas d'altitude absolue fiable (GPS vertical trop bruité) : on n'utilise QUE le
champ de gradients mesuré. En chaque point on connaît :
    - la pente (magnitude)      -> |∇z| = tan(angle) = pente_% / 100
    - l'orientation de descente -> ∇z (montée) pointe à l'opposé (aspect + 180°)
On reconstruit donc les HAUTEURS RELATIVES z(x,y) telles que leur gradient colle
au mieux aux mesures (intégration de Poisson par moindres carrés), après un
lissage du champ de gradients (spline plaque mince). La forme attendue, talus
sur les bords -> dôme, doit émerger si les pentes pointent vers l'extérieur.

Sorties (dossier de la trace) :
  <stem>_dome3d.png   : surface 3D lissée
  <stem>_dome_topo.png: carte topo (courbes de niveau relatives) + points + pentes

Usage :
  uv run reconstruct_dome.py "data/...snapshot....geojson"
  uv run reconstruct_dome.py ... --pas 0.4 --lissage 0.5
"""

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import lsqr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--pas", type=float, default=0.4, help="pas de la grille (m)")
    ap.add_argument("--lissage", type=float, default=0.5, help="lissage du champ de gradients (RBF smoothing)")
    ap.add_argument("--marge", type=float, default=0.5, help="marge autour du nuage (m)")
    ap.add_argument("--rmax", type=float, default=None, help="ecarte les points a plus de rmax m du centre median")
    ap.add_argument("--suffixe", default="", help="suffixe de nom de sortie")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    data = json.loads(src.read_text(encoding="utf-8"))
    S = [f for f in data["features"] if f["properties"].get("type") == "snapshot"
         and f["properties"].get("orientation_deg") is not None]

    lat = np.array([f["geometry"]["coordinates"][1] for f in S])
    lon = np.array([f["geometry"]["coordinates"][0] for f in S])
    pct = np.array([f["properties"]["pente_pct"] for f in S], float)
    az = np.array([f["properties"]["orientation_deg"] for f in S], float)   # descente

    lat0, lon0 = lat.mean(), lon.mean()
    mlon = math.cos(math.radians(lat0)) * 111320.0
    x = (lon - lon0) * mlon
    y = (lat - lat0) * 110540.0

    # Écarte les points isolés trop loin du centre (médiane robuste)
    if args.rmax:
        r = np.hypot(x - np.median(x), y - np.median(y))
        keep = r <= args.rmax
        print(f"Filtre rmax={args.rmax} m : {keep.sum()}/{len(x)} points gardés")
        x, y, pct, az = x[keep], y[keep], pct[keep], az[keep]
        S = [s for s, k in zip(S, keep) if k]

    # Gradient (montée) : magnitude = tan(angle) = pente/100 ; direction = aspect+180°
    mag = pct / 100.0
    up = np.radians(az + 180.0)
    gx = mag * np.sin(up)     # ∂z/∂Est
    gy = mag * np.cos(up)     # ∂z/∂Nord
    print(f"Snapshots utiles : {len(S)}")
    print(f"Pente            : med {np.median(pct):.0f}%  max {pct.max():.0f}%")

    # ---- Grille régulière
    pas = args.pas
    x0, x1 = x.min() - args.marge, x.max() + args.marge
    y0, y1 = y.min() - args.marge, y.max() + args.marge
    gxs = np.arange(x0, x1 + pas, pas)
    gys = np.arange(y0, y1 + pas, pas)
    GX, GY = np.meshgrid(gxs, gys)          # (ny, nx)
    ny, nx = GX.shape
    nodes = np.column_stack([GX.ravel(), GY.ravel()])

    # ---- Champ de gradients lissé (spline plaque mince)
    pts = np.column_stack([x, y])
    fgx = RBFInterpolator(pts, gx, kernel="thin_plate_spline", smoothing=args.lissage)
    fgy = RBFInterpolator(pts, gy, kernel="thin_plate_spline", smoothing=args.lissage)
    GGX = fgx(nodes).reshape(ny, nx)
    GGY = fgy(nodes).reshape(ny, nx)

    # ---- Intégration de Poisson par moindres carrés (différences finies)
    #   pour chaque arête : (z_voisin - z_courant)/pas = gradient au milieu
    def idx(i, j):
        return i * nx + j
    rows, cols, vals, rhs = [], [], [], []
    eq = 0
    for i in range(ny):
        for j in range(nx):
            if j + 1 < nx:                       # arête horizontale (Est)
                gmid = 0.5 * (GGX[i, j] + GGX[i, j + 1])
                rows += [eq, eq]; cols += [idx(i, j + 1), idx(i, j)]; vals += [1.0, -1.0]
                rhs.append(gmid * pas); eq += 1
            if i + 1 < ny:                       # arête verticale (Nord)
                gmid = 0.5 * (GGY[i, j] + GGY[i + 1, j])
                rows += [eq, eq]; cols += [idx(i + 1, j), idx(i, j)]; vals += [1.0, -1.0]
                rhs.append(gmid * pas); eq += 1
    # ancrage (moyenne nulle) : équation faible sum(z)=0
    n = nx * ny
    rows += [eq] * n; cols += list(range(n)); vals += [1e-3] * n; rhs.append(0.0); eq += 1
    A = coo_matrix((vals, (rows, cols)), shape=(eq, n)).tocsr()
    z = lsqr(A, np.array(rhs), atol=1e-10, btol=1e-10)[0].reshape(ny, nx)

    # ---- Masque : on ne garde que l'intérieur de l'enveloppe convexe des mesures
    from scipy.spatial import ConvexHull
    hull = ConvexHull(pts)
    poly = MplPath(pts[hull.vertices])
    inside = poly.contains_points(nodes, radius=args.marge).reshape(ny, nx)
    z = z - np.nanmin(z[inside])               # base à 0
    Zm = np.where(inside, z, np.nan)

    amp = np.nanmax(Zm)
    print(f"Amplitude relief : {amp:.2f} m (hauteurs relatives, intégrées des pentes)")
    print(f"Grille           : {nx} x {ny} ({pas} m)")

    outdir = Path(args.outdir) if args.outdir else src.parent
    stem = src.stem

    # ---- 3D
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_surface(GX, GY, Zm, cmap="terrain", linewidth=0, antialiased=True,
                    rstride=1, cstride=1, alpha=.95)
    ax.scatter(x, y, np.interp(0, [0, 1], [0, 1]) * 0 + 0, s=0)  # noop garde l'échelle
    ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)"); ax.set_zlabel("Hauteur rel. (m)")
    ax.set_title(f"{stem}\nRelief reconstruit (dôme) — amplitude {amp:.2f} m")
    try:
        ax.set_box_aspect((x1 - x0, y1 - y0, max(amp, .5) * 3))
    except Exception:
        pass
    # Vue Nord-en-haut / Est-à-droite : caméra au sud, en hauteur
    ax.view_init(elev=48, azim=-90)
    ax.text2D(0.50, 0.97, "↑ NORD", transform=ax.transAxes, ha="center", fontweight="bold")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_dome3d{args.suffixe}.png", dpi=140); plt.close(fig)

    # ---- Topo 2D : FOND = pente (%) lissée ; COURBES noires = relief (m)
    pente_grid = np.where(inside, 100.0 * np.hypot(GGX, GGY), np.nan)
    fig, ax = plt.subplots(figsize=(9, 8))
    cf = ax.contourf(GX, GY, pente_grid, levels=np.linspace(0, 70, 15),
                     cmap="RdYlGn_r", vmin=0, vmax=70, extend="max", alpha=.9)
    cl = ax.contour(GX, GY, Zm, levels=np.linspace(0, amp, 10), colors="k", linewidths=.5, alpha=.6)
    ax.clabel(cl, fmt="%.1f m", fontsize=6)
    u = np.sin(np.radians(az)) * pct           # flèche = descente
    v = np.cos(np.radians(az)) * pct
    ax.quiver(x, y, u, v, angles="xy", scale_units="xy", scale=1 / 0.04,
              width=.004, color="#222", alpha=.7, zorder=5, headwidth=4)
    sc = ax.scatter(x, y, c=pct, cmap="RdYlGn_r", vmin=0, vmax=70, s=70,
                    edgecolor="k", linewidth=.5, zorder=6)
    ax.set_aspect("equal"); ax.set_xlabel("Est (m) →"); ax.set_ylabel("Nord (m) ↑")
    ax.set_title(f"{stem}\nFond = pente (%) · courbes noires = relief (m) · flèches = descente")
    # Labels cardinaux sur les 4 côtés (nord en haut, est à droite)
    lbl = dict(transform=ax.transAxes, fontweight="bold", fontsize=13, color="#1565c0")
    ax.text(0.5, 1.005, "N", ha="center", va="bottom", **lbl)
    ax.text(0.5, -0.06, "S", ha="center", va="top", **lbl)
    ax.text(1.005, 0.5, "E", ha="left", va="center", **lbl)
    ax.text(-0.04, 0.5, "O", ha="right", va="center", **lbl)
    fig.colorbar(cf, label="Pente (%)", shrink=.8)
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_dome_topo{args.suffixe}.png", dpi=140); plt.close(fig)

    print(f"\nSorties :")
    print(f"  {stem}_dome3d.png")
    print(f"  {stem}_dome_topo.png")


if __name__ == "__main__":
    main()
