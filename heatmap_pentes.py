# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "matplotlib>=3.8"]
# ///
"""
Carte 2D des pentes (heatmap 0–100 %) sur fond OpenStreetMap, à partir d'un export
carto-pentes (.geojson).

Dispositif : iPhone sur l'axe de 2 roues (≈40 cm). Le ROULIS mesure alors de façon
fiable la pente EN TRAVERS (perpendiculaire au sens d'avance) — les 2 roues servent
de base. Le tangage, lui, n'est pas contraint (libre autour de l'axe) : on ne s'en
sert pas. La pente est donc lue directement :
    pente_% = tan(|roulis|) × 100
Pensé pour décider où un robot tondeuse peut passer (risque de dévers/retournement).

Sorties (dossier de la trace) :
  - <nom>_pentes.html : carte Leaflet interactive (OSM) + heatmap + légende
  - <nom>_pentes.png  : aperçu statique (vérification)

Usage :
    uv run heatmap_pentes.py "chemin/trace.geojson"
    uv run heatmap_pentes.py trace.geojson --seuil 40            # surligne > 40 %
    uv run heatmap_pentes.py trace.geojson --champ angle         # autre grandeur
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
<title>carto-pentes — %s</title>
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
const PTS = %s;          // [[lat, lon, pente_pct], ...]
const SEUIL = %s;        // seuil d'alerte (%%) ou null
const SMAX = %s;         // haut de l'échelle de couleur (%%)
const map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:22,maxNativeZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
function heat(p){p=Math.max(0,Math.min(SMAX,p));const h=120-(p/SMAX)*120;return `hsl(${h},85%%,46%%)`;}
const pts=[];
for(const [lat,lon,p] of PTS){
  pts.push([lat,lon]);
  const over = SEUIL!=null && p>SEUIL;
  L.circleMarker([lat,lon],{radius:over?8:6,color:over?'#000':'#fff',
    weight:over?2:1,fillColor:heat(p),fillOpacity:.95})
   .addTo(map).bindPopup(`<b>${p.toFixed(0)} %%</b> (${(Math.atan(p/100)*180/Math.PI).toFixed(1)}°)`);
}
map.fitBounds(pts,{padding:[30,30]});
const lg=L.control({position:'bottomright'});
lg.onAdd=function(){const d=L.DomUtil.create('div','legend');
  d.innerHTML=`Pente (%%)<div class="bar"></div><div class="sc"><span>0</span><span>${SMAX/2}</span><span>${SMAX}+</span></div>`
   +(SEUIL!=null?`<div style="margin-top:4px">⬤ &gt; ${SEUIL}%% (cerclé noir)</div>`:'');
  return d;};
lg.addTo(map);
</script></body></html>
"""

FIELDS = {"roulis": "roulis_deg", "tangage": "tangage_deg", "angle": "angle_deg"}
LABELS = {"roulis": "Pente en travers", "tangage": "Pente longitudinale", "angle": "Pente totale"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--champ", choices=list(FIELDS), default="roulis",
                    help="grandeur de pente (roulis = travers, fiable sur 2 roues)")
    ap.add_argument("--seuil", type=float, default=None, help="seuil d'alerte en %% (cercle noir)")
    ap.add_argument("--echelle", type=float, default=45.0, help="haut de l'echelle de couleur en %% (defaut 45)")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    src = Path(args.geojson)
    data = json.loads(src.read_text(encoding="utf-8"))
    ech = [f for f in data["features"] if f["properties"].get("type") == "echantillon"]
    if not ech:
        print("Aucun échantillon.")
        return

    lat = np.array([f["geometry"]["coordinates"][1] for f in ech])
    lon = np.array([f["geometry"]["coordinates"][0] for f in ech])
    val = np.array([f["properties"].get(FIELDS[args.champ]) or 0.0 for f in ech], float)
    pct = np.tan(np.radians(np.clip(np.abs(val), 0, 89))) * 100.0
    label = LABELS[args.champ]

    pmax = float(np.nanmax(pct))
    print(f"Échantillons     : {len(ech)}   (grandeur : {label})")
    print(f"Pente %          : min {pct.min():.0f}  méd {np.median(pct):.0f}  max {pmax:.0f}")
    if args.seuil is not None:
        n = int((pct > args.seuil).sum())
        print(f"Points > {args.seuil:.0f}%   : {n} ({100 * n / len(pct):.0f} %)")

    outdir = Path(args.outdir) if args.outdir else src.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    pts = [[round(float(a), 7), round(float(o), 7), round(float(p), 1)] for a, o, p in zip(lat, lon, pct)]
    titre = f"{label} (%) — max {pmax:.0f}%"
    html = HTML % (titre, titre, json.dumps(pts),
                   "null" if args.seuil is None else f"{args.seuil:g}",
                   f"{args.echelle:g}")
    (outdir / f"{stem}_pentes.html").write_text(html, encoding="utf-8")

    x = (lon - lon.mean()) * math.cos(math.radians(lat.mean())) * 111320
    y = (lat - lat.mean()) * 110540
    fig, ax = plt.subplots(figsize=(7, 7))
    sc = ax.scatter(x, y, c=pct, cmap="RdYlGn_r", vmin=0, vmax=args.echelle, s=22, edgecolor="#0002", linewidth=.3)
    ax.set_aspect("equal"); ax.set_xlabel("Est (m)"); ax.set_ylabel("Nord (m)")
    ax.set_title(f"{stem}\n{titre}")
    fig.colorbar(sc, label="Pente (%)")
    fig.tight_layout(); fig.savefig(outdir / f"{stem}_pentes.png", dpi=120); plt.close(fig)

    print(f"\nSorties dans : {outdir}")
    print(f"  {stem}_pentes.html  (ouvrir dans un navigateur — fond OSM interactif)")
    print(f"  {stem}_pentes.png")


if __name__ == "__main__":
    main()
