package app.template.patches.mimo

import app.morphe.patcher.Fingerprint
import app.morphe.patcher.string
import com.android.tools.smali.dexlib2.AccessFlags

/**
 * Mimo keeps the resolved subscription in a Kotlin data class that ProGuard
 * renamed. The only stable anchor is the generated `toString()`, which starts
 * with `SubscriptionState(entitlements=` and then prints the fields in
 * declaration order: entitlement list, highest entitlement, highest tier and
 * most recent expired entitlement.
 *
 * Every premium decision is derived from the `highestTier` field of this class:
 *
 * ```
 * boolean isPaid()     { return tier != SubscriptionTier.Free; }
 * boolean isProOrPromo(){ return tier != SubscriptionTier.Free || "rc_promo_pro_custom"...; }
 * ```
 *
 * and `SubscriptionManager.isAtLeastTier(tier)` compares the tier level byte of
 * that same field against the requested tier, which is what course, path,
 * certificate and AI tutor gating all go through.
 */
internal object SubscriptionStateClassFingerprint : Fingerprint(
    returnType = "Ljava/lang/String;",
    parameters = emptyList(),
    accessFlags = listOf(AccessFlags.PUBLIC, AccessFlags.FINAL),
    filters = listOf(
        string("SubscriptionState(entitlements="),
        string(", highestTier=")
    )
)

/**
 * The single constructor of the subscription state:
 *
 * ```
 * SubscriptionState(List, EntitlementInfo, SubscriptionTier, EntitlementInfo)
 * ```
 *
 * Only stable types are matched here (kotlin `List`, RevenueCat
 * `EntitlementInfo` and the app's own `SubscriptionTier`), so the fingerprint
 * does not depend on the renamed class or its fields.
 */
internal object SubscriptionStateCtorFingerprint : Fingerprint(
    classFingerprint = SubscriptionStateClassFingerprint,
    name = "<init>",
    parameters = listOf(
        "Ljava/util/List;",
        "Lcom/revenuecat/purchases/EntitlementInfo;",
        "Lcom/getmimo/data/source/remote/iap/model/SubscriptionTier;",
        "Lcom/revenuecat/purchases/EntitlementInfo;"
    )
)
