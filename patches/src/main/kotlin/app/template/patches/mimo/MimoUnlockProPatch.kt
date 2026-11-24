package app.template.patches.mimo

import app.morphe.patcher.extensions.InstructionExtensions.addInstructions
import app.morphe.patcher.patch.patch
import app.template.patches.shared.Constants.COMPATIBILITY_MIMO

/**
 * Mimo resolves the active subscription from RevenueCat into a single
 * `SubscriptionState` value object and reads the tier from it everywhere:
 * course and path unlocks, certificates, the AI tutor, keys and profile badges
 * all compare that tier against `Pro` or `Max`, and the "is paid" helpers used to
 * hide the upsell are derived from it as well.
 *
 * Rather than patching every consumer, the resolved tier is replaced with `Pro`
 * inside the only constructor of the state, so every "at least Pro" check
 * succeeds and the paywall is suppressed.
 *
 * Trade-off: the substitution is unconditional, so an account that really owns
 * `Max` is also reported as `Pro` and `Max`-only content stays locked. Nothing
 * premium is lost for a free account, which is what this patch targets.
 *
 * The patch is local only: no RevenueCat request or receipt is modified, so
 * server-side entitlement validation is untouched.
 */
@Suppress("unused")
val mimoUnlockProPatch = patch(
    name = "Unlock Pro",
    description = "Unlocks the Pro subscription features without a purchase.",
    default = true
) {
    compatibleWith(COMPATIBILITY_MIMO)

    execute {
        // The constructor has `.locals 0` because it only stores its parameters,
        // so the tier argument `p3` is rewritten in place with no extra registers.
        SubscriptionStateCtorFingerprint.method.addInstructions(
            0,
            """
                const-string p3, "Pro"
                invoke-static {p3}, Lcom/getmimo/data/source/remote/iap/model/SubscriptionTier;->valueOf(Ljava/lang/String;)Lcom/getmimo/data/source/remote/iap/model/SubscriptionTier;
                move-result-object p3
            """
        )
    }
}
