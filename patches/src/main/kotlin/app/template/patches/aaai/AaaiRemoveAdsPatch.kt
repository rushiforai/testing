package app.template.patches.aaai

import app.morphe.patcher.extensions.InstructionExtensions.addInstructions
import app.morphe.patcher.patch.patch
import app.template.patches.shared.Constants.COMPATIBILITY_AAAI

/**
 * AAAI (`AA Apps Installer`) ships the StartApp ad SDK only: a banner on the
 * main screen and on the GitHub search screen, plus an interstitial that
 * `MainActivity.runAfterAdGate` shows every third install.
 *
 * The app has no billing or premium entitlement, so the ads are blocked at the
 * SDK delivery points: banner and interstitial loads return immediately, and
 * the banner view is marked gone so the layout does not keep an empty strip.
 */
@Suppress("unused")
val aaaiRemoveAdsPatch = patch(
    name = "Remove ads",
    description = "Removes the StartApp banner and interstitial ads.",
    default = true
) {
    compatibleWith(COMPATIBILITY_AAAI)

    execute {
        // Banner load request. Both banner types inherit this method unchanged.
        StartAppBannerLoadFingerprint.method.addInstructions(0, "return-void")

        // Banner setup still runs, but the view is hidden so it takes no space.
        // `init()` declares .locals 3, so v0 is available.
        StartAppBannerInitFingerprint.method.addInstructions(
            0,
            """
                const/16 v0, 0x8
                invoke-virtual {p0, v0}, Landroid/view/View;->setVisibility(I)V
            """
        )

        // Interstitial load request. Every loadAd(...) overload funnels here.
        StartAppInterstitialLoadFingerprint.method.addInstructions(0, "return-void")

        // Interstitial display. Returning false makes the caller take its
        // "not displayed" path instead of waiting for an ad that never loaded.
        StartAppInterstitialShowFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x0
                return v0
            """
        )
    }
}
