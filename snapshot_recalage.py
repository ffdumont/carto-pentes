# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8"]
# ///
"""
Recalage d'un nuage de snapshots sur un polygone de référence (crête de talus
tracée dans Google Earth), pour absorber l'erreur GPS.

Principe (demande utilisateur) :
  1. Enveloppe convexe du nuage de mesures.
  2. « Balancer » le nuage sur le polygone : transformation de similitude
     (rotation + échelle uniforme + translation) qui aligne le repère propre
     (PCA / boîte orientée) du nuage sur celui du polygone. On garde la rotation
     MINIMALE (l'erreur GPS n'est pas un retournement). On ne corrige que la
     POSITION : l'azimut de pente (gravité + boussole) reste inchangé.
  3. « Pousser » les plus fortes pentes sur le BORD du polygone (projection sur
     l'arête la plus proche) — ce sont elles qui matérialisent la crête.

Sorties (dossier de la trace) :
  <stem>_recale.png   : avant/après + polygone + fortes pentes plaquées
  <stem>_recale.html  : carte OSM (positions recalées + crête plaquée)
  <stem>_recale.geojson : nuage recalé (positions corrigées)

Usage :
  uv run snapshot_recalage.py "data/...snapshot....geojson" --polygone data/cre-talus.kmz
  uv run snapshot_recalage.py ... --seuil 47        # seuil "forte pente" (%) a plaquer
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
import numpy as np


# --------------------------------------------------------------------------- géométrie
def read_polygon(path):
    p = Path(path)
    if p.suffix.lower() == ".kmz":
        with zipfile.ZipFile(p) as z:
            kml = z.read(next(n for n in z.namelist() if n.lower().endswith(".kml"))).decode("utf-8")
    else:
        kml = p.read_text(encoding="utf-8")
    m = re.search(r"<coordinates>(.*?)</coordinates>", kml, re.S)
    pts = []
    for tok in m.group(1).split():
        a = tok.split(",")
        if len(a) >= 2:
            pts.append((float(a[0]), float(a[1])))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def convex_hull(pts):
    """Andrew monotone chain. pts: Nx2 array -> hull (fermé) en array."""
    P = sorted(map(tuple, pts))
    if len(P) <= 2:
        return np.array(P)
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lo = []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up = []
    for p in reversed(P):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    h = lo[:-1] + up[:-1]
    return np.array(h + [h[0]])


def pca_frame(xy):
    c = xy.mean(0)
    C = np.cov((xy - c).T)
    w, V = np.linalg.eigh(C)
    o = np.argsort(w)[::-1]
    return c, np.sqrt(w[o]), V[:, o]


def proj_point_seg(p, a, b):
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-12), 0, 1)
    return a + t * ab


def snap_to_boundary(p, poly):
    best, bd = None, 1e18
    for i in range(len(poly) - 1):
        q = proj_point_seg(p, poly[i], poly[i + 1])
        d = np.hypot(*(p - q))
        if d < bd:
            bd, best = d, q
    return best, bd


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--polygone", required=True)
    ap.add_argument("--seuil", type=float, default=47.0, help="forte pente a plaquer sur le bord (%)")
    ap.add_argument("--mode", choices=["rigide", "similitude"], default="rigide",
                    help="rigide = rotation+translation (defaut, fidele) ; "
                         "similitude = + echelle (remplit le polygone, amplifie le bruit GPS)")
    ap.add_argument("--echelle", type=float, default=70.0)
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    data = json.loads(src.read_text(encoding="utf-8"))
    S = [f for f in data["features"] if f["properties"].get("type") == "snapshot"]
    lat = np.array([f["geometry"]["coordinates"][1] for f in S])
    lon = np.array([f["geometry"]["coordinates"][0] for f in S])
    pct = np.array([f["properties"]["pente_pct"] for f in S], float)
    az = np.array([f["properties"].get("orientation_deg") if f["properties"].get("orientation_deg") is not None
                   else np.nan for f in S], float)

    lat0, lon0 = lat.mean(), lon.mean()
    mlon = math.cos(math.radians(lat0)) * 111320.0
    mlat = 110540.0
    M = np.column_stack([(lon - lon0) * mlon, (lat - lat0) * mlat])   # nuage (E,N) m

    poly_ll = read_polygon(args.polygone)
    Pll = np.array(poly_ll)
    Pm = np.column_stack([(Pll[:, 0] - lon0) * mlon, (Pll[:, 1] - lat0) * mlat])
    Pm = np.vstack([Pm, Pm[0]])                                       # ferme le polygone

    # --- enveloppe convexe du nuage
    hull = convex_hull(M)

    # --- repères propres (PCA) du nuage et du polygone
    cM, sM, _ = pca_frame(M)
    cP, sP, _ = pca_frame(Pm[:-1])
    angM = pca_frame(M)[2]
    angP = pca_frame(Pm[:-1])[2]
    aM = math.atan2(angM[1, 0], angM[0, 0])
    aP = math.atan2(angP[1, 0], angP[0, 0])

    # rotation MINIMALE alignant les axes principaux (mod 180°, ramenée dans [-90,90])
    dtheta = ((aP - aM + math.pi / 2) % math.pi) - math.pi / 2
    # échelle uniforme = ratio des aires des boîtes (préserve les proportions).
    # En mode rigide on ne déforme pas (scale=1) : l'erreur GPS est ~1 m, pas un
    # facteur d'échelle ; agrandir le nuage ne ferait qu'amplifier la dispersion.
    scale = 1.0 if args.mode == "rigide" else math.sqrt((sP[0] * sP[1]) / (sM[0] * sM[1] + 1e-12))
    R = np.array([[math.cos(dtheta), -math.sin(dtheta)],
                  [math.sin(dtheta), math.cos(dtheta)]])

    def transform(xy):
        return (scale * (xy - cM) @ R.T) + cP

    Mr = transform(M)
    hull_r = transform(hull)

    print(f"Snapshots        : {len(S)}")
    print(f"Rotation recalage: {math.degrees(dtheta):+.1f} deg")
    print(f"Echelle recalage : x{scale:.2f}")
    print(f"Translation centre: dE={cP[0]-cM[0]:+.2f} dN={cP[1]-cM[1]:+.2f} m")

    # --- plaquage des fortes pentes sur le bord du polygone
    strong = pct >= args.seuil
    snapped = np.array(Mr, copy=True)
    snap_info = []
    for i in np.where(strong)[0]:
        q, d = snap_to_boundary(Mr[i], Pm)
        snapped[i] = q
        snap_info.append((i, d))
    if snap_info:
        dd = [d for _, d in snap_info]
        print(f"Fortes pentes >={args.seuil:.0f}%: {strong.sum()} plaquees (deplacement moy {np.mean(dd):.1f} m, max {np.max(dd):.1f} m)")

    outdir = Path(args.outdir) if args.outdir else src.parent
    stem = src.stem

    # ----------------------------------------------------------------- PNG avant/apres
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.plot(Pm[:, 0], Pm[:, 1], "-", color="#1565c0", lw=2.2, zorder=3, label="polygone réf. (crête)")
    ax.plot(hull[:, 0], hull[:, 1], ":", color="#999", lw=1, zorder=2, label="enveloppe mesures (avant)")
    # avant (gris) + liens vers après
    ax.scatter(M[:, 0], M[:, 1], s=28, c="#bbb", edgecolor="#888", linewidth=.3, zorder=3, label="mesures (avant)")
    for i in range(len(M)):
        tgt = snapped[i] if strong[i] else Mr[i]
        ax.plot([M[i, 0], tgt[0]], [M[i, 1], tgt[1]], "-", color="#ccc", lw=.5, zorder=2)
    # après : points recalés colorés par pente + flèches de descente
    u = np.sin(np.radians(az)) * pct
    v = np.cos(np.radians(az)) * pct
    m = ~np.isnan(az)
    ax.quiver(Mr[m, 0], Mr[m, 1], u[m], v[m], angles="xy", scale_units="xy",
              scale=1 / 0.045, width=.004, color="#111", alpha=.8, zorder=5,
              headwidth=4, headlength=5)
    sc = ax.scatter(Mr[:, 0], Mr[:, 1], c=pct, cmap="RdYlGn_r", vmin=0, vmax=args.echelle,
                    s=85, edgecolor="k", linewidth=.5, zorder=6, label="mesures (recalées)")
    # fortes pentes plaquées sur le bord (étoiles)
    if strong.any():
        ax.scatter(snapped[strong, 0], snapped[strong, 1], marker="*", s=240,
                   c=pct[strong], cmap="RdYlGn_r", vmin=0, vmax=args.echelle,
                   edgecolor="k", linewidth=.8, zorder=8, label=f"fortes pentes ≥{args.seuil:.0f}% (plaquées)")
    ax.set_aspect("equal"); ax.grid(True, ls=":", alpha=.3)
    ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)")
    ax.set_title(f"{stem}\nRecalage sur la crête — rot {math.degrees(dtheta):+.0f}°, échelle ×{scale:.2f}")
    ax.annotate("N", xy=(0.04, 0.96), xycoords="axes fraction", ha="center", fontsize=12, fontweight="bold")
    ax.annotate("", xy=(0.04, 0.96), xytext=(0.04, 0.88), xycoords="axes fraction",
                arrowprops=dict(arrowstyle="<-", color="k"))
    ax.legend(loc="lower right", fontsize=7.5)
    fig.colorbar(sc, label="Pente (%)", shrink=.8)
    fig.tight_layout()
    png = outdir / f"{stem}_recale.png"
    fig.savefig(png, dpi=140); plt.close(fig)

    # ----------------------------------------------------------------- retour lat/lon
    def to_ll(xy):
        return [round(lat0 + xy[1] / mlat, 7), round(lon0 + xy[0] / mlon, 7)]

    rec_pts = []
    for i in range(len(S)):
        pos = snapped[i] if strong[i] else Mr[i]
        ll = to_ll(pos)
        rec_pts.append({"lat": ll[0], "lon": ll[1], "pct": round(float(pct[i]), 1),
                        "az": (None if np.isnan(az[i]) else round(float(az[i]), 0)),
                        "ori": S[i]["properties"].get("orientation", "—"),
                        "strong": bool(strong[i])})

    # GeoJSON recalé
    feats = []
    for i in range(len(S)):
        pr = dict(S[i]["properties"])
        pr["recale"] = True
        pr["plaque_bord"] = bool(strong[i])
        feats.append({"type": "Feature", "properties": pr,
                      "geometry": {"type": "Point", "coordinates": [rec_pts[i]["lon"], rec_pts[i]["lat"]]}})
    feats.append({"type": "Feature", "properties": {"type": "polygone_ref"},
                  "geometry": {"type": "Polygon", "coordinates": [[[c[0], c[1]] for c in poly_ll] + [[poly_ll[0][0], poly_ll[0][1]]]]}})
    (outdir / f"{stem}_recale.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")

    # HTML
    poly_js = json.dumps([[round(c[1], 7), round(c[0], 7)] for c in poly_ll])
    html = HTML % (stem, f"Recalé sur la crête — max {pct.max():.0f}%",
                   json.dumps(rec_pts), f"{args.echelle:g}", poly_js)
    (outdir / f"{stem}_recale.html").write_text(html, encoding="utf-8")

    print(f"\nSorties :")
    print(f"  {png.name}")
    print(f"  {stem}_recale.html")
    print(f"  {stem}_recale.geojson")


HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>recalage — %s</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
 integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<style>
 html,body,#map{height:100%%;margin:0}
 .legend{background:#fff;padding:8px 10px;border-radius:8px;box-shadow:0 1px 5px #0003;font:12px sans-serif;line-height:1.4}
 .legend .bar{height:10px;width:160px;border-radius:5px;margin:4px 0;
   background:linear-gradient(90deg,hsl(120,85%%,45%%),hsl(60,85%%,48%%),hsl(0,85%%,48%%))}
 .legend .sc{display:flex;justify-content:space-between}
 .title{position:absolute;top:8px;left:50%%;transform:translateX(-50%%);z-index:1000;
   background:#fff;padding:6px 12px;border-radius:8px;box-shadow:0 1px 5px #0003;font:600 14px sans-serif}
</style></head><body>
<div class="title">%s</div><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
 integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const PTS=%s, SMAX=%s, POLY=%s;
const map=L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:22,maxNativeZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
L.polygon(POLY,{color:'#1565c0',weight:2,fill:false}).addTo(map);
function heat(p){p=Math.max(0,Math.min(SMAX,p));return `hsl(${120-(p/SMAX)*120},85%%,46%%)`;}
const lls=[],mLat=110540,mLon=111320*Math.cos(PTS[0].lat*Math.PI/180);
for(const s of PTS){
  lls.push([s.lat,s.lon]);
  if(s.az!=null){const L=s.pct*0.06,a=s.az*Math.PI/180;
    window.L.polyline([[s.lat,s.lon],[s.lat+L*Math.cos(a)/mLat,s.lon+L*Math.sin(a)/mLon]],{color:'#111',weight:2,opacity:.7}).addTo(map);}
  L.circleMarker([s.lat,s.lon],{radius:s.strong?9:6,color:s.strong?'#000':'#fff',weight:s.strong?2:1,
    fillColor:heat(s.pct),fillOpacity:.95}).addTo(map)
   .bindPopup(`<b>${s.pct.toFixed(0)} %%</b> → ${s.ori} (${s.az==null?'?':s.az.toFixed(0)}°)${s.strong?'<br><i>plaqué sur la crête</i>':''}`);
}
map.fitBounds(lls,{padding:[40,40]});
const lg=L.control({position:'bottomright'});
lg.onAdd=function(){const d=L.DomUtil.create('div','legend');
 d.innerHTML=`Pente (%%)<div class="bar"></div><div class="sc"><span>0</span><span>${SMAX}+</span></div>`
  +`<div style="margin-top:4px">⬤ noir = forte pente plaquée</div><div>— polygone = crête réf.</div>`;return d;};
lg.addTo(map);
</script></body></html>
"""


if __name__ == "__main__":
    main()
