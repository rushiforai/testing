package app.template.patches.aaad

import app.morphe.patcher.extensions.InstructionExtensions.addInstructions
import app.morphe.patcher.patch.patch
import app.template.patches.shared.Constants.COMPATIBILITY_AAAD

/**
 * AAAD sells a one-time PRO license that removes the download cooldown, the
 * 3-day newcomer limit and the PRO badge/version screen.
 *
 * The entitlement lives in a Firebase Realtime Database node and is read by
 * `ProStatusHelper`. Every PRO surface (main screen, payment screen, license
 * transfer, PRO code redemption, debug helpers) goes through that helper, so
 * forcing its two readers to return true unlocks the whole feature set.
 *
 * The main screen also has a local fallback used when the realtime listener
 * has no snapshot yet (fresh install, signed out, or a database error). That
 * fallback writes `isProUser = false`, so it is overridden as well; otherwise
 * the first launch would still be treated as a free user.
 */
@Suppress("unused")
val aaadUnlockProPatch = patch(
    name = "Unlock pro",
    description = "Unlocks the PRO version without a purchase.",
    default = true
) {
    compatibleWith(COMPATIBILITY_AAAD)

    execute {
        // isProUser(DataSnapshot) is static with .locals 3, so v0 is free.
        ProStatusHelperIsProUserFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x1
                return v0
            """
        )

        // isProValue(Object) is static with .locals 2, so v0 is free.
        ProStatusHelperIsProValueFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x1
                return v0
            """
        )

        // checkLegacyProStatus(boolean) is an instance method with .locals 9,
        // so v0 is free and p1 is the hideLoadingOnComplete flag.
        MainActivityCheckLegacyProStatusFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x1
                iput-boolean v0, p0, Lcom/legs/appsforaa/MainActivityNew;->isProUser:Z
                iput-boolean v0, p0, Lcom/legs/appsforaa/MainActivityNew;->isProStatusLoaded:Z
                invoke-virtual {p0}, Lcom/legs/appsforaa/MainActivityNew;->updateProStatusUI()V
                invoke-virtual {p0, p1}, Lcom/legs/appsforaa/MainActivityNew;->checkEligibility(Z)V
                return-void
            """
        )
    }
}
