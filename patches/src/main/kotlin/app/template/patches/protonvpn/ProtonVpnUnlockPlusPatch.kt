package app.template.patches.protonvpn

import app.morphe.patcher.extensions.InstructionExtensions.addInstructions
import app.morphe.patcher.patch.patch
import app.template.patches.shared.Constants.COMPATIBILITY_PROTONVPN

/**
 * Proton VPN decides every Plus feature that is enforced on the device through
 * two getters on `VpnUser`: `isFreeUser` and `isUserPlusOrAbove`. Between them
 * they gate split tunneling, custom DNS, LAN connections, VPN accelerator, the
 * NAT type and the default connection, and they also hide the NetShield upsell.
 *
 * Server access is deliberately left alone. It is computed from `maxTier` by
 * `hasAccessToServer()`, which this patch never touches, so Plus-only locations
 * keep failing the local check and the server-side subscription validation is
 * unchanged.
 */
@Suppress("unused")
val protonVpnUnlockPlusPatch = patch(
    name = "Unlock Plus features",
    description = "Unlocks the Plus features that are enforced on the device while premium locations stay locked.",
    default = true
) {
    compatibleWith(COMPATIBILITY_PROTONVPN)

    execute {
        // `isFreeUser` has one local register, so a constant can be returned from it.
        VpnUserIsFreeFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x0
                return v0
            """
        )

        // `isUserPlusOrAbove` has two local registers, so `v0` is available here too.
        VpnUserIsPlusFingerprint.method.addInstructions(
            0,
            """
                const/4 v0, 0x1
                return v0
            """
        )
    }
}
