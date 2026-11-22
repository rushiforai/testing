package app.template.patches.aaai

import app.morphe.patcher.Fingerprint
import com.android.tools.smali.dexlib2.AccessFlags

/**
 * AAAI shows all of its ads through the bundled StartApp SDK. There is no
 * billing or premium entitlement in the app, so the ads are blocked at the SDK
 * delivery points instead.
 *
 * Both screens (`MainActivity` and `GithubSearchActivity`) declare a
 * `com.startapp.sdk.ads.banner.Banner` in their layout, and `MainActivity`
 * loads a `StartAppAd` interstitial that is shown every third install through
 * `runAfterAdGate`. Blocking the two SDK entry points covers every ad surface.
 */

/**
 * Banner load request. `BannerStandard` (and `Banner3D`) inherit this method
 * unchanged, so patching the base class blocks every banner fetch, including
 * the automatic load on attach and the scheduled refreshes.
 */
internal object StartAppBannerLoadFingerprint : Fingerprint(
    definingClass = "Lcom/startapp/sdk/ads/banner/BannerBase;",
    name = "load",
    accessFlags = listOf(AccessFlags.PROTECTED),
    returnType = "V",
    parameters = listOf("Ljava/lang/String;")
)

/**
 * Banner view setup. `BannerBase.init()` is called from the constructor of
 * every banner type, so hiding the view here covers both layouts.
 */
internal object StartAppBannerInitFingerprint : Fingerprint(
    definingClass = "Lcom/startapp/sdk/ads/banner/BannerBase;",
    name = "init",
    accessFlags = listOf(AccessFlags.PROTECTED),
    returnType = "V"
)

/**
 * Interstitial load request. Every `loadAd(...)` overload funnels into this
 * method, which is also what `runAfterAdGate` triggers through `loadAd()`.
 */
internal object StartAppInterstitialLoadFingerprint : Fingerprint(
    definingClass = "Lcom/startapp/sdk/adsbase/StartAppAd;",
    name = "load",
    accessFlags = listOf(AccessFlags.PUBLIC),
    returnType = "V",
    parameters = listOf(
        "Lcom/startapp/sdk/adsbase/model/AdPreferences;",
        "Lcom/startapp/sdk/adsbase/adlisteners/AdEventListener;",
        "Ljava/lang/String;"
    )
)

/**
 * Interstitial display. Every `showAd(...)` overload funnels into this method.
 * It returns false so callers fall through to their "ad not displayed" path
 * instead of waiting on an ad that was never loaded.
 */
internal object StartAppInterstitialShowFingerprint : Fingerprint(
    definingClass = "Lcom/startapp/sdk/adsbase/StartAppAd;",
    name = "showAd",
    accessFlags = listOf(AccessFlags.PUBLIC),
    returnType = "Z",
    parameters = listOf(
        "Ljava/lang/String;",
        "Lcom/startapp/sdk/adsbase/adlisteners/AdDisplayListener;"
    )
)
