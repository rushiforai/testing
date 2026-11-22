package app.template.patches.protonvpn

import app.morphe.patcher.Fingerprint
import app.morphe.patcher.fieldAccess
import app.morphe.patcher.literal
import app.morphe.patcher.methodCall
import app.morphe.patcher.opcode
import app.morphe.patcher.string
import com.android.tools.smali.dexlib2.AccessFlags
import com.android.tools.smali.dexlib2.Opcode

/**
 * `VpnUser` is the Room entity that holds the signed-in plan. The class name is
 * obfuscated, but the Kotlin data class generates a `toString()` that starts with
 * `VpnUser(userId=` and lists the entity fields in declaration order, which makes
 * it a stable anchor for the class itself.
 */
internal object VpnUserClassFingerprint : Fingerprint(
    returnType = "Ljava/lang/String;",
    parameters = emptyList(),
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.FINAL),
    filters = listOf(
        string("VpnUser(userId="),
        string(", maxTier=")
    )
)

/**
 * `val isFreeUser get() = maxTier == FREE_TIER` in `VpnUser`:
 *
 * ```
 * iget-object v0, p0, L..;->l:Ljava/lang/Integer;  // maxTier
 * if-nez v0, :cond_0
 * goto :goto_0
 * :cond_0
 * invoke-virtual {v0}, Ljava/lang/Integer;->intValue()I
 * move-result v0
 * if-nez v0, :cond_1
 * const/4 v0, 0x1
 * return v0
 * ```
 *
 * `maxTier` is the only nullable `Integer` field read this way, so the field type
 * plus the `Integer.intValue()` call is enough to identify the getter without
 * relying on the obfuscated class or method names.
 */
internal object VpnUserIsFreeFingerprint : Fingerprint(
    classFingerprint = VpnUserClassFingerprint,
    returnType = "Z",
    parameters = emptyList(),
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.FINAL),
    filters = listOf(
        fieldAccess(opcode = Opcode.IGET_OBJECT, definingClass = "this", type = "Ljava/lang/Integer;"),
        opcode(Opcode.IF_NEZ),
        opcode(Opcode.GOTO),
        methodCall(definingClass = "Ljava/lang/Integer;", name = "intValue", returnType = "I"),
        opcode(Opcode.MOVE_RESULT),
        opcode(Opcode.IF_NEZ),
        literal(1),
        opcode(Opcode.RETURN)
    )
)

/**
 * `val isUserPlusOrAbove get() = userTier >= PLUS_TIER` in `VpnUser`:
 *
 * ```
 * invoke-virtual {p0}, L..;->a()I   // userTier
 * move-result v0
 * const/4 v1, 0x2                   // PLUS_TIER
 * if-lt v0, v1, :cond_0
 * const/4 v0, 0x1
 * return v0
 * ```
 *
 * The `userTier` getter is called on `this` and returns `I`, so it is matched by
 * its declaring class and signature rather than by its obfuscated name.
 */
internal object VpnUserIsPlusFingerprint : Fingerprint(
    classFingerprint = VpnUserClassFingerprint,
    returnType = "Z",
    parameters = emptyList(),
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.FINAL),
    filters = listOf(
        methodCall(definingClass = "this", returnType = "I", parameters = emptyList()),
        opcode(Opcode.MOVE_RESULT),
        literal(2),
        opcode(Opcode.IF_LT),
        literal(1),
        opcode(Opcode.RETURN)
    )
)

/**
 * `VpnUser.getNetShieldAvailability()` is inlined into a static helper:
 *
 * ```
 * if (vpnUser == null) goto upgrade;
 * if (vpnUser.isFreeUser) goto upgrade;
 * return NetShieldAvailability.AVAILABLE;
 * upgrade: return NetShieldAvailability.UPGRADE_VPN_PLUS;
 * ```
 *
 * NetShield filtering happens on Proton's DNS servers, so the client only forwards
 * a `netshield-level` flag to the VPN backend. Because this patch forces
 * `isFreeUser` to false, this helper would otherwise start reporting NetShield as
 * available, which is a server-side feature and must stay locked. The two
 * `sget-object` filters are the `AVAILABLE` and `UPGRADE_VPN_PLUS` enum constants.
 */
internal object NetShieldAvailabilityFingerprint : Fingerprint(
    isStatic = true,
    returnType = "L",
    parameters = listOf("L"),
    filters = listOf(
        methodCall(reference = { VpnUserIsFreeFingerprint.originalMethod }),
        opcode(Opcode.MOVE_RESULT),
        opcode(Opcode.IF_EQZ),
        opcode(Opcode.GOTO),
        opcode(Opcode.SGET_OBJECT),
        opcode(Opcode.RETURN_OBJECT),
        opcode(Opcode.SGET_OBJECT),
        opcode(Opcode.RETURN_OBJECT)
    )
)
