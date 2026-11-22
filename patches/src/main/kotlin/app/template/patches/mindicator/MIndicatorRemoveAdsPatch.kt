package app.template.patches.mindicator

import app.morphe.patcher.extensions.InstructionExtensions.addInstructions
import app.morphe.patcher.patch.patch
import app.template.patches.shared.Constants.COMPATIBILITY_MINDICATOR

/**
 * mIndicator shows AdMob/AdX banner, native and interstitial ads, plus a
 * server-hosted web ad on the exit screen. The app has no billing or premium
 * entitlement, so the ads are blocked directly at the SDK delivery points.
 */
@Suppress("unused")
val mIndicatorRemoveAdsPatch = patch(
    name = "Remove ads",
    description = "Removes banner, native, interstitial and exit screen ads.",
    default = true
) {
    compatibleWith(COMPATIBILITY_MINDICATOR)

    execute {
        // Banners: hide the bar so it leaves no empty space, then skip the request.
        BannerAdLoadFingerprint.method.addInstructions(
            0,
            """
                const/16 v0, 0x8
                invoke-virtual {p0, v0}, Landroid/view/View;->setVisibility(I)V
                return-void
            """
        )

        // Native AdMob ads.
        NativeAdLoadFingerprint.method.addInstructions(0, "return-void")

        // Native AdX/DFP ads.
        NativeManagerAdLoadFingerprint.method.addInstructions(0, "return-void")

        // Full screen interstitial ads.
        InterstitialAdLoadFingerprint.method.addInstructions(0, "return-void")

        // Server-hosted web ad shown on the exit screen.
        ExitWebAdFingerprint.method.addInstructions(0, "return-void")
    }
}
