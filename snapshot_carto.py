# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8"]
# ///
"""
Carto des relevés ponctuels « snapshot » (export snapshot.html).

Chaque snapshot porte une PENTE (acos(|az|/|g|), indépendante de la pose) et une
ORIENTATION = azimut de la plus grande pente (sens de la descente). On en tire
deux représentations :

  PNG  : fond = pente interpolée (triangulation), points colorés par pente %,
         + FLÈCHES dans le sens de la descente (longueur ∝ pente).
         → l'ARÊTE d'un talus se lit là où les flèches s'opposent / divergent.
  HTML : carte Leaflet (fond OSM) — marqueurs colorés + flèche de descente +
         popup (pente °/%, orientation, qualité).

Usage :
    uv run snapshot_carto.py "data/...snapshot....geojson"
    uv run snapshot_carto.py fichier.geojson --echelle 70   # haut d'échelle en %
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
import matplotlib.tri as mtri
import numpy as np

HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>carto-pentes snapshot — %s</title>
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
const PTS = %s;     // [{lat,lon,pct,deg,az,ori,std}]
const SMAX = %s;    // haut de l'échelle de couleur (%%)
const POLY = %s;    // [[lat,lon], ...] polygone de référence ou null
const map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:22,maxNativeZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
if(POLY){ window.L.polygon(POLY,{color:'#1565c0',weight:2,fill:false,dashArray:'4 4'}).addTo(map); }
function heat(p){p=Math.max(0,Math.min(SMAX,p));const h=120-(p/SMAX)*120;return `hsl(${h},85%%,46%%)`;}
const lls=[];
const mPerDegLat=110540, mPerDegLon=111320*Math.cos(PTS[0].lat*Math.PI/180);
for(const s of PTS){
  lls.push([s.lat,s.lon]);
  // Flèche de descente : longueur ∝ pente (0.06 m par %% de pente)
  const L=s.pct*0.06, az=s.az*Math.PI/180;
  const dlat=(L*Math.cos(az))/mPerDegLat, dlon=(L*Math.sin(az))/mPerDegLon;
  if(s.az!=null){
    L.polyline?0:0;
    const line=[[s.lat,s.lon],[s.lat+dlat,s.lon+dlon]];
    window.L.polyline(line,{color:'#111',weight:2,opacity:.7}).addTo(map);
    // pointe
    window.L.circleMarker([s.lat+dlat,s.lon+dlon],{radius:2,color:'#111',fillColor:'#111',fillOpacity:1}).addTo(map);
  }
  window.L.circleMarker([s.lat,s.lon],{radius:s.pct>=47?9:7,color:s.pct>=47?'#000':'#fff',
    weight:s.pct>=47?2:1,fillColor:heat(s.pct),fillOpacity:.95})
   .addTo(map).bindPopup(`<b>${s.pct.toFixed(0)} %%</b> (${s.deg.toFixed(1)}°) — descente vers <b>${s.ori}</b> (${s.az==null?'?':s.az.toFixed(0)}°)<br>qualité ±${s.std.toFixed(2)}°`);
}
map.fitBounds(lls,{padding:[40,40]});
const lg=L.control({position:'bottomright'});
lg.onAdd=function(){const d=L.DomUtil.create('div','legend');
  d.innerHTML=`Pente (%%)<div class="bar"></div><div class="sc"><span>0</span><span>${Math.round(SMAX/2)}</span><span>${SMAX}+</span></div>`
   +`<div style="margin-top:4px">→ flèche = sens de la descente</div>`
   +`<div>⬤ cerclé noir = ≥ 47 %%</div>`;
  return d;};
lg.addTo(map);
</script></body></html>
"""


def read_polygon(path):
    """Lit un polygone depuis un .kml ou .kmz (Google Earth). -> [(lon,lat), ...]"""
    p = Path(path)
    if p.suffix.lower() == ".kmz":
        with zipfile.ZipFile(p) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            txt = z.read(name).decode("utf-8")
    else:
        txt = p.read_text(encoding="utf-8")
    m = re.search(r"<coordinates>(.*?)</coordinates>", txt, re.S)
    if not m:
        return []
    coords = []
    for tok in m.group(1).split():
        parts = tok.split(",")
        if len(parts) >= 2:
            coords.append((float(parts[0]), float(parts[1])))
    return coords


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--echelle", type=float, default=70.0, help="haut de l'echelle couleur en %% (defaut 70)")
    ap.add_argument("--polygone", default=None, help="polygone de reference (.kml/.kmz) a superposer")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    data = json.loads(src.read_text(encoding="utf-8"))
    snaps = [f for f in data["features"] if f["properties"].get("type") == "snapshot"]
    if not snaps:
        print("Aucun snapshot dans ce fichier.")
        return

    lat = np.array([f["geometry"]["coordinates"][1] for f in snaps])
    lon = np.array([f["geometry"]["coordinates"][0] for f in snaps])
    pct = np.array([f["properties"]["pente_pct"] for f in snaps], float)
    deg_ = np.array([f["properties"]["pente_deg"] for f in snaps], float)
    az = np.array([f["properties"].get("orientation_deg") if f["properties"].get("orientation_deg") is not None
                   else np.nan for f in snaps], float)
    std = np.array([f["properties"].get("ecart_type_deg") or 0.0 for f in snaps], float)
    ori = [f["properties"].get("orientation", "—") for f in snaps]

    print(f"Snapshots        : {len(snaps)}")
    print(f"Pente %          : min {pct.min():.0f}  med {np.median(pct):.0f}  max {pct.max():.0f}")
    print(f"Zone (approx.)   : {(lon.max()-lon.min())*111320*math.cos(math.radians(lat.mean())):.0f} m E-O "
          f"x {(lat.max()-lat.min())*110540:.0f} m N-S")

    outdir = Path(args.outdir) if args.outdir else src.parent
    stem = src.stem

    # Projection locale ENU (mètres) centrée sur le barycentre des snapshots
    lat0, lon0 = lat.mean(), lon.mean()
    mlon = math.cos(math.radians(lat0)) * 111320
    x = (lon - lon0) * mlon
    y = (lat - lat0) * 110540

    # Polygone de référence (optionnel) + estimation du décalage GPS
    poly = read_polygon(args.polygone) if args.polygone else []
    px = py = None
    if poly:
        plon = np.array([c[0] for c in poly]); plat = np.array([c[1] for c in poly])
        px = (plon - lon0) * mlon; py = (plat - lat0) * 110540
        pcx, pcy = px.mean(), py.mean()                 # centre du polygone
        strong = pct >= 47
        if strong.any():
            scx, scy = x[strong].mean(), y[strong].mean()   # centre des points raides
            dx, dy = pcx - scx, pcy - scy
            dist = math.hypot(dx, dy)
            brg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
            print(f"Polygone ref.    : {len(poly)-1} sommets")
            print(f"Decalage GPS est.: {dist:.1f} m vers {brg:.0f} deg "
                  f"(points >=47% -> polygone)")

    fig, ax = plt.subplots(figsize=(8.5, 8))

    # Fond : pente interpolée par triangulation (contexte continu entre points)
    try:
        tri = mtri.Triangulation(x, y)
        cf = ax.tricontourf(tri, pct, levels=np.linspace(0, args.echelle, 15),
                            cmap="RdYlGn_r", alpha=.55, extend="max")
    except Exception:
        cf = None

    # Flèches dans le sens de la descente (longueur ∝ pente)
    u = np.sin(np.radians(az)) * pct  # Est
    v = np.cos(np.radians(az)) * pct  # Nord
    m = ~np.isnan(az)
    ax.quiver(x[m], y[m], u[m], v[m], angles="xy", scale_units="xy",
              scale=1 / 0.045, width=.004, color="#111", alpha=.8, zorder=4,
              headwidth=4, headlength=5)

    # Polygone de référence (crête tracée dans Google Earth)
    if px is not None:
        ax.plot(px, py, "-", color="#1565c0", lw=2, zorder=7, label="polygone réf. (crête)")
        ax.legend(loc="lower right", fontsize=8)

    # Points colorés par pente
    sc = ax.scatter(x, y, c=pct, cmap="RdYlGn_r", vmin=0, vmax=args.echelle,
                    s=90, edgecolor="k", linewidth=.6, zorder=5)
    # Étiquette pente % (cerclage des points raides)
    for xi, yi, p in zip(x, y, pct):
        ax.annotate(f"{p:.0f}", (xi, yi), fontsize=6.5, ha="center", va="center",
                    color="white" if p > args.echelle * .45 else "black", zorder=6)

    ax.set_aspect("equal")
    ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)")
    ax.set_title(f"{stem}\nPente (%) + sens de descente — l'arête est là où les flèches s'opposent")
    ax.grid(True, ls=":", alpha=.3)
    # Rose des vents
    ax.annotate("N", xy=(0.04, 0.96), xycoords="axes fraction", ha="center",
                fontsize=12, fontweight="bold")
    ax.annotate("", xy=(0.04, 0.96), xytext=(0.04, 0.88), xycoords="axes fraction",
                arrowprops=dict(arrowstyle="<-", color="k"))
    fig.colorbar(sc, label="Pente (%)", shrink=.8)
    fig.tight_layout()
    png = outdir / f"{stem}_carto.png"
    fig.savefig(png, dpi=140); plt.close(fig)

    # HTML interactif
    pts = [{"lat": round(float(a), 7), "lon": round(float(o), 7), "pct": round(float(p), 1),
            "deg": round(float(d), 1), "az": (None if math.isnan(z) else round(float(z), 0)),
            "ori": orr, "std": round(float(s), 2)}
           for a, o, p, d, z, orr, s in zip(lat, lon, pct, deg_, az, ori, std)]
    titre = f"Snapshots — pente max {pct.max():.0f}% · {len(snaps)} pts"
    poly_js = "null" if not poly else json.dumps([[round(c[1], 7), round(c[0], 7)] for c in poly])
    html = HTML % (stem, titre, json.dumps(pts), f"{args.echelle:g}", poly_js)
    htmlf = outdir / f"{stem}_carto.html"
    htmlf.write_text(html, encoding="utf-8")

    print(f"\nSorties :")
    print(f"  {png.name}   (vue analytique : arête via flèches)")
    print(f"  {htmlf.name}  (carte OSM interactive — ouvrir dans un navigateur)")


if __name__ == "__main__":
    main()
