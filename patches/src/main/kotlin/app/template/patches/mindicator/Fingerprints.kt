package app.template.patches.mindicator

import app.morphe.patcher.Fingerprint
import app.morphe.patcher.string
import com.android.tools.smali.dexlib2.AccessFlags

/**
 * mIndicator delivers every ad through the bundled Google Mobile Ads SDK
 * (AdMob/AdX), so the stable SDK entry points are the anchors.
 *
 * The app has no billing or premium entitlement to patch; blocking the ad
 * loads is the whole change.
 */

/** Banner ads. The app loads AdMob and AdX banners through the inherited method. */
internal object BannerAdLoadFingerprint : Fingerprint(
    definingClass = "Lcom/google/android/gms/ads/BaseAdView;",
    name = "loadAd",
    accessFlags = listOf(AccessFlags.PUBLIC),
    returnType = "V",
    parameters = listOf("Lcom/google/android/gms/ads/AdRequest;")
)

/** Native AdMob ads (`AdLoader.Builder(...).loadAd(AdRequest)`). */
internal object NativeAdLoadFingerprint : Fingerprint(
    definingClass = "Lcom/google/android/gms/ads/AdLoader;",
    name = "loadAd",
    accessFlags = listOf(AccessFlags.PUBLIC),
    returnType = "V",
    parameters = listOf("Lcom/google/android/gms/ads/AdRequest;")
)

/** Native AdX/DFP ads (`AdLoader.Builder(...).loadAd(AdManagerAdRequest)`). */
internal object NativeManagerAdLoadFingerprint : Fingerprint(
    definingClass = "Lcom/google/android/gms/ads/AdLoader;",
    name = "loadAd",
    accessFlags = listOf(AccessFlags.PUBLIC),
    returnType = "V",
    parameters = listOf("Lcom/google/android/gms/ads/admanager/AdManagerAdRequest;")
)

/** Full screen interstitial ads. */
internal object InterstitialAdLoadFingerprint : Fingerprint(
    definingClass = "Lcom/google/android/gms/ads/interstitial/InterstitialAd;",
    name = "load",
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.STATIC),
    returnType = "V",
    parameters = listOf(
        "Landroid/content/Context;",
        "Ljava/lang/String;",
        "Lcom/google/android/gms/ads/AdRequest;",
        "Lcom/google/android/gms/ads/interstitial/InterstitialAdLoadCallback;"
    )
)

/**
 * The exit screen falls back to a server-hosted web ad in a WebView when remote
 * config disables the native exit ad. The method name is obfuscated, so the ad
 * URL string literal is the anchor instead.
 */
internal object ExitWebAdFingerprint : Fingerprint(
    definingClass = "Lcom/mobond/mindicator/ui/AdUI;",
    accessFlags = listOf(AccessFlags.PUBLIC),
    returnType = "V",
    parameters = listOf("Landroid/app/Activity;"),
    filters = listOf(
        string("https://mobond.com/getads?")
    )
)
