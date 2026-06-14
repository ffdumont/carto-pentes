# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8"]
# ///
"""
Carte de pente MAX (toutes directions) par reconstruction du gradient, rendue sur
OpenStreetMap.

Dispositif : iPhone sur l'axe de 2 roues → le ROULIS mesure la pente dans la
direction perpendiculaire au déplacement. Une seule passe ne donne donc que la
composante transversale. Mais en parcourant une zone dans plusieurs directions
(spirale), on accumule des mesures :

    tan(roulis_i) = gradient · n_i        (n_i ⟂ au cap de déplacement)

et on résout le gradient ∇z=(gx,gy) par moindres carrés pondérés, localement, sur
une grille. La pente max = |∇z| (indépendante de la direction) → ce que « voit »
un robot tondeuse quel que soit son sens de passage.

Sorties (dossier de la trace) :
  - <nom>_gradient.html : cellules colorées (pente max %) sur fond OSM + légende
  - <nom>_gradient.png  : aperçu statique

Usage :
    uv run gradient_map.py "trace.geojson"
    uv run gradient_map.py trace.geojson --maille 1.0 --rayon 3 --echelle 45
"""

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>carto-pentes — pente max</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
 integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<style>
 html,body,#map{height:100%%;margin:0}
 .legend{background:#fff;padding:8px 10px;border-radius:8px;box-shadow:0 1px 5px #0003;font:12px sans-serif;line-height:1.4}
 .legend .bar{height:10px;width:160px;border-radius:5px;margin:4px 0;
   background:linear-gradient(90deg,hsl(120,85%%,45%%),hsl(90,85%%,45%%),hsl(60,85%%,48%%),hsl(30,85%%,48%%),hsl(0,85%%,48%%))}
 .legend .sc{display:flex;justify-content:space-between}
 .title{position:absolute;top:8px;left:50%%;transform:translateX(-50%%);z-index:1000;
   background:#fff;padding:6px 12px;border-radius:8px;box-shadow:0 1px 5px #0003;font:600 14px sans-serif}
</style></head><body>
<div class="title">%s</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
 integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const CELLS = %s;   // [[lat0,lon0,lat1,lon1,pct], ...]
const TRACK = %s;   // [[lat,lon], ...]
const SMAX = %s;
const map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:22,maxNativeZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
function heat(p){p=Math.max(0,Math.min(SMAX,p));const h=120-(p/SMAX)*120;return `hsl(${h},85%%,46%%)`;}
for(const [la0,lo0,la1,lo1,p] of CELLS){
  L.rectangle([[la0,lo0],[la1,lo1]],{stroke:false,fillColor:heat(p),fillOpacity:.6})
   .addTo(map).bindPopup(`<b>${p.toFixed(0)} %%</b> (pente max)`);
}
L.polyline(TRACK,{color:'#0008',weight:1.5,opacity:.5}).addTo(map);
map.fitBounds(TRACK,{padding:[30,30]});
const lg=L.control({position:'bottomright'});
lg.onAdd=function(){const d=L.DomUtil.create('div','legend');
  d.innerHTML=`Pente max (%%)<div class="bar"></div><div class="sc"><span>0</span><span>${SMAX/2}</span><span>${SMAX}+</span></div>`;
  return d;};
lg.addTo(map);
</script></body></html>
"""


def moving_average(a, w):
    if w < 2:
        return a
    k = np.ones(w) / w
    return np.convolve(a, k, mode="same")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--maille", type=float, default=1.0, help="taille de cellule (m)")
    ap.add_argument("--rayon", type=float, default=3.0, help="rayon d'influence des mesures (m)")
    ap.add_argument("--echelle", type=float, default=45.0, help="haut de l'echelle couleur (%%)")
    ap.add_argument("--min-couv", type=float, default=1.0, help="poids cumulé minimal par cellule")
    ap.add_argument("--diversite", type=float, default=0.12, help="diversité de directions min (0-0.5)")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    data = json.loads(src.read_text(encoding="utf-8"))
    ech = [f for f in data["features"] if f["properties"].get("type") == "echantillon"]
    ech.sort(key=lambda f: f["properties"].get("timestamp", ""))

    lon = np.array([f["geometry"]["coordinates"][0] for f in ech])
    lat = np.array([f["geometry"]["coordinates"][1] for f in ech])
    roll = np.array([f["properties"].get("roulis_deg") or 0.0 for f in ech], float)
    capg = np.array([f["properties"].get("cap_gps_deg") if f["properties"].get("cap_gps_deg") is not None
                     else np.nan for f in ech], float)

    lon0, lat0 = lon.mean(), lat.mean()
    mx = math.cos(math.radians(lat0)) * 111320.0
    my = 110540.0
    x = (lon - lon0) * mx
    y = (lat - lat0) * my

    # Direction de déplacement : cap GPS si dispo, sinon dérivée d'une trace lissée
    xs, ys = moving_average(x, 5), moving_average(y, 5)
    tx = np.zeros(len(x)); ty = np.zeros(len(x))
    for i in range(len(x)):
        if np.isfinite(capg[i]):
            cr = math.radians(capg[i])
            tx[i], ty[i] = math.sin(cr), math.cos(cr)       # (Est, Nord)
        else:
            a, b = max(0, i - 3), min(len(x) - 1, i + 3)
            tx[i], ty[i] = xs[b] - xs[a], ys[b] - ys[a]
    norm = np.hypot(tx, ty)
    valid = norm > 1e-6
    tx[valid] /= norm[valid]; ty[valid] /= norm[valid]

    # n ⟂ déplacement ; mesure transversale c = tan(roulis)
    nx, ny = -ty, tx
    c = np.tan(np.radians(roll))
    sx, sy = x[valid], y[valid]
    snx, sny, sc = nx[valid], ny[valid], c[valid]

    # Grille
    pad = args.rayon
    gx = np.arange(x.min() - pad, x.max() + pad, args.maille)
    gy = np.arange(y.min() - pad, y.max() + pad, args.maille)
    R = args.rayon
    cells = []        # (cx, cy, slope_pct)
    grid = np.full((len(gy), len(gx)), np.nan)

    for jy, cy in enumerate(gy):
        for jx, cx in enumerate(gx):
            d2 = (sx - cx) ** 2 + (sy - cy) ** 2
            sel = d2 < (3 * R) ** 2
            if sel.sum() < 3:
                continue
            w = np.exp(-d2[sel] / (R * R))
            sw = w.sum()
            if sw < args.min_couv:
                continue
            wnx, wny, wc = snx[sel], sny[sel], sc[sel]
            a11 = np.sum(w * wnx * wnx); a12 = np.sum(w * wnx * wny); a22 = np.sum(w * wny * wny)
            b1 = np.sum(w * wnx * wc); b2 = np.sum(w * wny * wc)
            # diversité de directions = plus petite valeur propre / trace
            tr = a11 + a22
            disc = math.sqrt(max(0.0, (tr / 2) ** 2 - (a11 * a22 - a12 * a12)))
            lam2 = tr / 2 - disc
            if tr <= 0 or lam2 / tr < args.diversite:
                continue
            det = a11 * a22 - a12 * a12
            ggx = (a22 * b1 - a12 * b2) / det
            ggy = (-a12 * b1 + a11 * b2) / det
            slope = math.hypot(ggx, ggy) * 100.0
            cells.append((cx, cy, slope))
            grid[jy, jx] = slope

    if not cells:
        print("Aucune cellule reconstruite (couverture/diversité insuffisantes).")
        return

    slopes = np.array([c[2] for c in cells])
    print(f"Échantillons utilisés : {valid.sum()}/{len(ech)}  (cap GPS sur {np.isfinite(capg).sum()})")
    print(f"Cellules reconstruites: {len(cells)}  (maille {args.maille} m, rayon {R} m)")
    print(f"Pente max %           : min {slopes.min():.0f}  méd {np.median(slopes):.0f}  max {slopes.max():.0f}")

    outdir = Path(args.outdir) if args.outdir else src.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    titre = f"Pente max (%) — reconstruite par gradient (max {slopes.max():.0f}%)"

    # HTML OSM : rectangles
    half = args.maille / 2
    cell_js = []
    for cx, cy, p in cells:
        la0 = lat0 + (cy - half) / my; la1 = lat0 + (cy + half) / my
        lo0 = lon0 + (cx - half) / mx; lo1 = lon0 + (cx + half) / mx
        cell_js.append([round(la0, 7), round(lo0, 7), round(la1, 7), round(lo1, 7), round(p, 1)])
    track = [[round(float(la), 7), round(float(lo), 7)] for la, lo in zip(lat, lon)]
    html = HTML % (titre, json.dumps(cell_js), json.dumps(track), f"{args.echelle:g}")
    (outdir / f"{stem}_gradient.html").write_text(html, encoding="utf-8")

    # Aperçu PNG
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    im = ax.pcolormesh(gx, gy, np.ma.masked_invalid(grid), cmap="RdYlGn_r", vmin=0, vmax=args.echelle, shading="nearest")
    ax.plot(x, y, color="#0007", lw=.6)
    ax.set_aspect("equal"); ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)")
    ax.set_title(f"{stem}\n{titre}")
    fig.colorbar(im, label="Pente max (%)")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_gradient.png", dpi=120); plt.close(fig)

    print(f"\nSorties dans : {outdir}")
    print(f"  {stem}_gradient.html  (fond OSM)")
    print(f"  {stem}_gradient.png")


if __name__ == "__main__":
    main()
