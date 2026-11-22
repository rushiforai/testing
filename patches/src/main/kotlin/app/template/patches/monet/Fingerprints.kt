package app.template.patches.monet

import app.morphe.patcher.Fingerprint
import app.morphe.patcher.fieldAccess
import app.morphe.patcher.literal
import app.morphe.patcher.opcode
import com.android.tools.smali.dexlib2.Opcode

/**
 * Monet keeps its whole Premium decision in one obfuscated getter.
 *
 * The getter reads four boolean fields of its own class (Play purchase state,
 * license state, license rejection, signature/tamper state) and returns true
 * when the entitlement is active:
 *
 * ```
 * boolean isActive() {
 *     return playPremium || (licenseValid && (licenseAccepted || !tampered));
 * }
 * ```
 *
 * Every Premium surface reads the observable built from this method
 * (`n70.e`, refreshed with `dz5.a()` on each state change), so forcing the
 * getter to return true unlocks all features at once.
 *
 * The class and method names are obfuscated, so the field layout and the
 * instruction sequence are the anchor. The exact sequence is:
 *
 * ```
 * iget-boolean v0, p0, L..;->?:Z   // play premium
 * iget-boolean v1, p0, L..;->?:Z   // license valid
 * iget-boolean v2, p0, L..;->?:Z   // license rejected
 * iget-boolean p0, p0, L..;->?:Z   // tampered
 * if-eqz v0, :cond
 * goto :goto
 * if-eqz v1, :cond
 * if-nez v2, :cond
 * if-nez p0, :cond
 * const/4 p0, 0x1
 * return p0
 * const/4 p0, 0x0
 * return p0
 * ```
 */
internal object PremiumEntitlementFingerprint : Fingerprint(
    isStatic = false,
    returnType = "Z",
    parameters = emptyList(),
    filters = listOf(
        fieldAccess(opcode = Opcode.IGET_BOOLEAN, definingClass = "this", type = "Z"),
        fieldAccess(opcode = Opcode.IGET_BOOLEAN, definingClass = "this", type = "Z"),
        fieldAccess(opcode = Opcode.IGET_BOOLEAN, definingClass = "this", type = "Z"),
        fieldAccess(opcode = Opcode.IGET_BOOLEAN, definingClass = "this", type = "Z"),
        opcode(Opcode.IF_EQZ),
        opcode(Opcode.IF_EQZ),
        opcode(Opcode.IF_NEZ),
        opcode(Opcode.IF_NEZ),
        literal(1),
        opcode(Opcode.RETURN),
        literal(0),
        opcode(Opcode.RETURN)
    )
)
