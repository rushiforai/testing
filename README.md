# 👋🧩 Morphe Patches template

Template repository for Morphe Patches.

## ❓ About

Patches for apps I like.

<!-- TODO: Update this about section with a brief introduction/summary about this repo and what it offers. -->

### How to use these patches

Click the button below, or manually add this repo url to Morphe Manager: https://github.com/xyz-user/xyz-patches

<a href="https://morphe.software/add-source?github=xyz-user/xyz-patches">
  <img src=".github/assets/add-to-morphe.svg" alt="Add to Morphe" height="48">
</a>

## 🩹 Patches list

<!-- PATCHES_START EXPANDED -->
> **[v1.0.0-dev.2](https://github.com/rushiforai/testing/releases/tag/v1.0.0-dev.2)**&nbsp;&nbsp;&middot;&nbsp;&nbsp;`dev`&nbsp;&nbsp;&middot;&nbsp;&nbsp;**2 patches** across **2 apps**&nbsp;&nbsp;&middot;&nbsp;&nbsp;[Full details](PATCHES.md)

| # | App | Patches | Version | Package |
|---|---|---|---|---|
| 1 | [**mIndicator**](PATCHES.md#mindicator-commobondmindicator) | 1 | `18.0.371` | [`com.mobond.mindicator`](https://play.google.com/store/apps/details?id=com.mobond.mindicator) |
| 2 | [**XYZ app**](PATCHES.md#xyz-app-comexampleapp) | 1 | `2.0.0`, `1.0.2` | [`com.example.app`](https://play.google.com/store/apps/details?id=com.example.app) |
<!-- PATCHES_END -->

### 🛠️ Building locally

- Run `./gradlew buildAndroid`
- The built patches .mpp file is found in `patches/build/libs/patches-*.mpp`
- Patch the mpp file using [Morphe-Desktop](https://github.com/MorpheApp/morphe-desktop)
  like any other patch bundle.

See the [Morphe documentation](https://github.com/MorpheApp/morphe-documentation) for more information.

## 📜 License

UserXYZ Patches are licensed under the [GNU General Public License v3.0](LICENSE)
