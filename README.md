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

## Mode de capture : hybride (retenu)

La pente continue « en marchant » n'est pas fiable : le capteur mesure alors le mouvement du téléphone, pas le terrain. L'altitude GPS (±10–20 m) ne permet pas non plus de dériver la pente à l'échelle d'un jardin, et Safari n'expose pas le baromètre.

→ **Approche retenue :**

1. **Trace GPS en continu** pendant qu'on parcourt le jardin (contexte du chemin).
2. **Capture ponctuelle de pente** : on s'arrête, on pose le téléphone à plat, on appuie sur « mesurer la pente ici » → fige `{lat, lon, angle, orientation}`.

Variante future possible : monter le téléphone sur un support solidaire du sol (planchette/chariot) pour logger la pente en continu de façon fiable.

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

## État

🌱 Initialisation — description du projet. Prochaine étape : prototype `index.html` (version hybride).
