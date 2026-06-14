# carto-pentes

Cartographier les pentes d'un jardin depuis un iPhone, via une page web servie en HTTPS — sans application native.

## Principe

Une page web unique, ouverte dans Safari sur iPhone, lit côté client :

- **Position & cap** — API `Geolocation` (`watchPosition`) : trace GPS continue + **cap de déplacement** (`heading`) et vitesse.
- **Inclinaison** — API `DeviceOrientation` (`beta`/`gamma`) : l'accéléromètre mesure la gravité → inclinaison **absolue** de l'appareil par rapport à l'horizontale, déjà calibrée comme le niveau de l'app Mesure d'Apple (**aucune tare**).

L'iPhone est monté sur l'**axe de deux roues** (cf. dispositif ci-dessous). Les mesures — dont les **capteurs bruts** — sont accumulées dans le navigateur puis exportées en **GeoJSON** ; le calcul des pentes se fait en **post-traitement** (scripts `uv`, relecture QGIS / Leaflet).

## Architecture cible

- **Aucun backend.** Tout le calcul est côté client.
- **Hébergement statique HTTPS** (GitHub Pages / Firebase Hosting). HTTPS est obligatoire pour les API capteurs sur iOS.
- Données stockées en mémoire + `localStorage` de secours, export GeoJSON manuel.

## Dispositif & mode de capture (retenu)

La pente « en marchant » téléphone à la main n'est pas fiable : le capteur mesure le mouvement du téléphone, pas le terrain. L'altitude GPS (±10–20 m) ne permet pas non plus de dériver la pente à l'échelle d'un jardin, et Safari n'expose pas le baromètre.

→ **Dispositif retenu :** iPhone fixé sur l'**axe de deux roues** (espacées ~40 cm — en pratique un **râteau scarificateur monté sur roues**). On **enregistre en continu** en promenant l'engin :

1. **Trace GPS continue** (`watchPosition`, ~1 point/s) + cap & vitesse de déplacement.
2. **Capteurs d'inclinaison bruts** (`beta`/`gamma`/`alpha`/cap magnéto) échantillonnés à chaque point GPS, **moyennés sur l'intervalle** (atténue les vibrations) avec leur **écart-type** comme indice de qualité.

**Ce que mesure le dispositif.** Avec deux roues, le **roulis** (basculement autour de l'axe de roulement) mesure de façon fiable la **pente en travers** (perpendiculaire au sens de marche) : les deux roues forment une base de 40 cm. Le **tangage** (autour de l'essieu) n'est **pas** contraint par le sol — l'iPhone tourne librement autour de l'axe — donc **inexploitable**, on l'ignore.

**Pas de calibration (tare).** L'accéléromètre donne l'inclinaison **absolue** via la gravité. Un éventuel défaut de montage est un **offset constant**, retiré en post-traitement.

**Post-traitement** (à partir des bruts + cap GPS) : extraction de la pente en travers par point, puis
- carte 2D **heatmap de pente (%)** sur fond OpenStreetMap ;
- en combinant des passages de **directions croisées** (parcours en **spirale**), reconstruction du **gradient** → pente *max* (toutes directions) et altitude relative.

L'appli exporte un GeoJSON (trace + un point/échantillon, symbolisable QGIS), affiche en direct le tracé coloré par la pente en travers, et un bouton « Marquer » pose des waypoints. Mise en page **portrait et paysage**.

Variantes abandonnées : capture ponctuelle (« on s'arrête, on pose, on mesure ») ; **tare ⊙ Zéro** ; **sélection manuelle de l'orientation de montage (⟳ Avant)** — toutes remplacées par « capteurs bruts + post-traitement ».

## Contraintes iOS

- **HTTPS obligatoire** pour `Geolocation` et `DeviceOrientation`.
- Accès aux capteurs de mouvement : nécessite un **clic explicite** (`DeviceOrientationEvent.requestPermission()`).
- **Pas d'enregistrement en arrière-plan** : la page est suspendue à la veille / au changement d'app. → garder l'écran allumé via l'API **Wake Lock** (`navigator.wakeLock`, Safari iOS 16.4+).
- `watchPosition({ enableHighAccuracy: true })` avec **throttle** (~1 pt/s) pour limiter le volume.

## Précision attendue

- GPS horizontal : ~3–5 m en ciel dégagé, dégradé près des bâtiments / sous les arbres — **facteur limitant** de la résolution spatiale.
- Inclinomètre : ~1° (capteur fiable) ; la qualité dépend des vibrations (atténuées par moyennage) et de l'alignement de l'axe sur les roues.
- Cap GPS fiable seulement **en mouvement** (> ~0,4 m/s) : parcourir à allure régulière.

## Pile technique envisagée

- HTML/JS statique, **Leaflet** pour la carte.
- Pas de build initialement (un seul fichier `index.html` auto-suffisant), à faire évoluer si besoin.

## Accès depuis l'iPhone

iOS exige du **HTTPS** pour `Geolocation` et `DeviceOrientation`. En HTTP simple (`http://192.168.x.x`) les capteurs restent muets : `requestPermission()` n'existe pas et aucune invite n'apparaît.

### Voie retenue — GitHub Pages (HTTPS reconnu)

Le dépôt est publié sur GitHub Pages : l'iPhone ouvre directement l'URL `https://ffdumont.github.io/carto-pentes/` (via les données cellulaires ou un Wi-Fi avec internet — les tuiles de carte en ont besoin de toute façon). Certificat reconnu → **aucun avertissement**, capteurs débloqués. C'est la cible de prod.

### Dev local — `serve.py` (optionnel)

Pour développer sur le PC, `serve.py` sert le dossier en HTTPS avec un certificat auto-signé (EC P-256, conforme iOS, IP locales dans le SAN) :

```sh
uv run serve.py          # port 8443
```

⚠️ Pièges rencontrés (consignés pour ne pas les refaire) :
- **iOS rejette un certificat auto-signé** (« la connexion réseau a été perdue », sans avertissement contournable) → pour tester en local sur iPhone il faut **installer le certificat comme profil de confiance**. Sur un Mac, `curl -k` l'ignore.
- Le certificat doit être **conforme iOS** : validité ≤ 398 j, SAN avec les IP, `ExtendedKeyUsage = serverAuth`.
- **MTU réduite** (switches virtuels Hyper-V) : un gros certificat RSA bloquait la poignée TLS sur le réseau → clé **EC P-256** (poignée compacte) pour contourner.
- Pare-feu Windows : autoriser le port entrant (`New-NetFirewallRule … -LocalPort 8443`).

## Analyse (post-traitement, scripts `uv`)

Sorties générées dans `data/` (ignoré par git). Lancer : `uv run <script> <trace.geojson>`.

- **`heatmap_pentes.py`** — carte 2D **pente en travers (%)** sur fond OSM (HTML Leaflet interactif + PNG), échelle de couleur paramétrable (`--echelle`, défaut 45 %).
- **`gradient_map.py`** — **pente max** par reconstruction du gradient (roulis multi-directions + cap GPS → moindres carrés par cellule), rendu cellules sur OSM.
- **`reconstruct3d.py`** — profil / surface 3D par intégration des pentes. *Caduc sur le dispositif 2 roues* (dépend du tangage) ; conservé pour mémoire.
- **`recalage_ign.py`** — recale un profil sur l'altimétrie **IGN RGE ALTI** (API Géoplateforme ouverte, sans clé).

## État

🌿 Capture fonctionnelle, publiée sur GitHub Pages — `index.html` auto-suffisant (Leaflet, trace GPS + cap/vitesse, inclinomètre absolu, **capteurs bruts loggés**, tracé coloré par la pente en travers, waypoints, Wake Lock, export GeoJSON, secours `localStorage`). `serve.py` pour le dev local HTTPS. Modèle de mesure arrêté (2 roues → roulis fiable, tangage ignoré, pas de tare ; correction en post-traitement). **Prochaine étape : relevé terrain calibré (spirale, directions croisées) avec cette version, puis coder le post-traitement** roulis+cap GPS → pente en travers → heatmap/gradient sur OSM.
