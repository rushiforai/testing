package app.template.patches.monet

import app.morphe.patcher.extensions.InstructionExtensions.addInstructions
import app.morphe.patcher.patch.patch
import app.template.patches.shared.Constants.COMPATIBILITY_MONET

/**
 * Monet sells a one-time Premium license (Google Play purchase or a Polar
 * license key) that gates wallpapers, icon packs, per-app look, app PIN,
 * profiles, parental controls, video screensavers and custom accents.
 *
 * All of those surfaces read a single entitlement getter, so forcing that
 * getter to return true unlocks every Premium feature.
 */
@Suppress("unused")
val monetUnlockPremiumPatch = patch(
    name = "Unlock premium",
    description = "Unlocks all Monet Premium features without a purchase.",
    default = true
) {
    compatibleWith(COMPATIBILITY_MONET)

    execute {
        // The getter only reads fields, so it has no locals of its own.
        // Returning a constant needs one local register: `v0` is available
        // because the method declares `.locals 3`.
        PremiumEntitlementFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x1
                return v0
            """
        )
    }
}
