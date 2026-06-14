# carto-pentes

Cartographier les pentes d'un jardin depuis un iPhone, via une page web servie en HTTPS — sans application native.

## Principe

Une page web unique, ouverte dans Safari sur iPhone, lit deux capteurs côté client :

- **Position** — API `Geolocation` (`watchPosition`), trace GPS en continu.
- **Inclinaison** — API `DeviceOrientation` (`beta`/`gamma`), le téléphone posé à plat sur le sol sert d'inclinomètre.

Les mesures sont accumulées dans le navigateur puis exportées en **GeoJSON** (trace + points de pente) pour relecture dans QGIS / Leaflet.

## Architecture cible

- **Aucun backend.** Tout le calcul est côté client.
- **Hébergement statique HTTPS** (GitHub Pages / Firebase Hosting). HTTPS est obligatoire pour les API capteurs sur iOS.
- Données stockées en mémoire + `localStorage` de secours, export GeoJSON manuel.

## Mode de capture : enregistrement continu sur chariot (retenu)

La pente « en marchant » téléphone à la main n'est pas fiable : le capteur mesure le mouvement du téléphone, pas le terrain. L'altitude GPS (±10–20 m) ne permet pas non plus de dériver la pente à l'échelle d'un jardin, et Safari n'expose pas le baromètre.

→ **Approche retenue :** monter l'iPhone **à plat sur un chariot à roues** solidaire du sol. Le téléphone épouse alors l'inclinaison du terrain, et on **enregistre en continu** :

1. **Trace GPS en continu** (`watchPosition`, ~1 point/s) pendant qu'on promène le chariot.
2. **Pente + orientation + cap échantillonnés à chaque point GPS** : chaque échantillon fige `{lat, lon, angle, orientation (azimut de descente), cap, alt, précision, t}`.

**Calibration « Zéro » (tare).** L'angle est mesuré comme l'**écart à une orientation de référence** (produit scalaire des vecteurs gravité), pas par rapport au plan horizontal absolu. On pose donc le chariot à plat dans sa **position de montage réelle** (téléphone à plat, en paysage, ou posé sur la tranche comme un niveau à bulle) et on appuie sur **⊙ Zéro** : cette pose devient 0°. La référence est persistée (`localStorage`).

Le **cap** (boussole) est enregistré en plus de la pente : position + pente + azimut de descente + cap permettront de **reconstruire la surface en 3D** (intégration du gradient le long du parcours).

L'appli affiche le tracé **coloré par la pente** en direct (vert → rouge) et exporte un GeoJSON (trace + un point par échantillon, symbolisable dans QGIS). Un bouton « Marquer » permet aussi des waypoints ponctuels remarquables. Mise en page adaptée **portrait et paysage** (panneau latéral en paysage pour ne pas masquer la carte).

Variante antérieure (abandonnée) : capture ponctuelle « on s'arrête, on pose le téléphone, on mesure » — remplacée par l'enregistrement continu sur chariot, plus rapide et plus dense.

## Contraintes iOS

- **HTTPS obligatoire** pour `Geolocation` et `DeviceOrientation`.
- Accès aux capteurs de mouvement : nécessite un **clic explicite** (`DeviceOrientationEvent.requestPermission()`).
- **Pas d'enregistrement en arrière-plan** : la page est suspendue à la veille / au changement d'app. → garder l'écran allumé via l'API **Wake Lock** (`navigator.wakeLock`, Safari iOS 16.4+).
- `watchPosition({ enableHighAccuracy: true })` avec **throttle** (~1 pt/s) pour limiter le volume.

## Précision attendue

- GPS horizontal : ~3–5 m en ciel dégagé, dégradé près des bâtiments / sous les arbres.
- Inclinomètre : ~1° si le téléphone est bien posé à plat.

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

## État

🌿 Prototype **enregistrement continu (chariot)** fonctionnel — `index.html` auto-suffisant (Leaflet, trace GPS continue, inclinomètre `DeviceOrientation`, tracé coloré par la pente, waypoints, Wake Lock, export GeoJSON, secours `localStorage`), publié sur GitHub Pages. `serve.py` pour le dev local. Prochaine étape : essai terrain sur le chariot et calibration de l'angle/orientation.
