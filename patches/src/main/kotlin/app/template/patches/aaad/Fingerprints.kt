package app.template.patches.aaad

import app.morphe.patcher.Fingerprint
import app.morphe.patcher.methodCall
import app.morphe.patcher.string
import com.android.tools.smali.dexlib2.AccessFlags

/**
 * AAAD keeps the PRO entitlement on a Firebase Realtime Database node and
 * every screen funnels its decision through [ProStatusHelper].
 *
 * `isProUser` is the single snapshot reader used by the main activity, the
 * payment screen, the license transfer screen and the debug helpers. Forcing
 * it to return true unlocks every PRO surface at once.
 *
 * The method is a plain static helper, so the class, name, access flags,
 * return type and parameter are all stable anchors:
 *
 * ```
 * public static final boolean isProUser(DataSnapshot snapshot)
 * ```
 */
internal object ProStatusHelperIsProUserFingerprint : Fingerprint(
    definingClass = "Lcom/legs/appsforaa/utils/ProStatusHelper;",
    name = "isProUser",
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.STATIC, AccessFlags.FINAL),
    returnType = "Z",
    parameters = listOf("Lcom/google/firebase/database/DataSnapshot;"),
    filters = listOf(
        string("snapshot"),
        methodCall(
            definingClass = "Lkotlin/jvm/internal/Intrinsics;",
            name = "areEqual"
        ),
        string("purchase_date")
    )
)

/**
 * `isProValue` is the sibling of [ProStatusHelperIsProUserFingerprint] used by
 * the PRO code redemption screen. It reads a raw database value instead of a
 * snapshot:
 *
 * ```
 * public static final boolean isProValue(Object value)
 * ```
 */
internal object ProStatusHelperIsProValueFingerprint : Fingerprint(
    definingClass = "Lcom/legs/appsforaa/utils/ProStatusHelper;",
    name = "isProValue",
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.STATIC, AccessFlags.FINAL),
    returnType = "Z",
    parameters = listOf("Ljava/lang/Object;"),
    filters = listOf(
        methodCall(
            definingClass = "Lkotlin/jvm/internal/Intrinsics;",
            name = "areEqual"
        ),
        string("purchase_date")
    )
)

/**
 * When the realtime listener has no snapshot (not signed in, database error,
 * or a brand new install) the main activity falls back to
 * `checkLegacyProStatus`, which writes `isProUser = false` and then runs the
 * eligibility check. That fallback would undo the helper patch, so it is
 * forced to report PRO as well.
 *
 * The method is `public final void checkLegacyProStatus(boolean)`. The auth
 * guard and its log message are stable string anchors:
 *
 * ```
 * if (AuthManager.getCurrentUid() == null) {
 *     Logger.w("MainActivityNew", "Cannot check legacy location - not authenticated");
 *     isProUser = false;
 *     ...
 * }
 * ```
 */
internal object MainActivityCheckLegacyProStatusFingerprint : Fingerprint(
    definingClass = "Lcom/legs/appsforaa/MainActivityNew;",
    name = "checkLegacyProStatus",
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.FINAL),
    returnType = "V",
    parameters = listOf("Z"),
    filters = listOf(
        methodCall(
            definingClass = "Lcom/legs/appsforaa/managers/AuthManager;",
            name = "getCurrentUid"
        ),
        string("Cannot check legacy location - not authenticated"),
        string("users")
    )
)
