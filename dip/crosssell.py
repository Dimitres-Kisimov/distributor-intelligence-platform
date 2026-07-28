"""Cross-sell association mining over the platform's synthetic order baskets.

The frequent-itemset miner and rule metrics below are adapted from
market-basket-analysis (the author's from-scratch Apriori implementation after
Agrawal & Srikant, VLDB 1994), (c) Dimitres Kisimov — dependency-by-copy, the
same pattern other projects use for the retail cleaning pipeline, so this repo
stays self-contained. Adaptations: support counting enumerates the k-item
combinations present in each basket (fast for the platform's 200-SKU world)
instead of testing every candidate against every basket, and rules are plain
dicts so the Flask layer can serialise them unchanged.

Honesty contract (stated here, in the API payload, on the dashboard card and
in the exported workbook):

* Everything is computed on the *seeded synthetic demo data*: one basket per
  synthetic customer order event, built deterministically in
  :func:`dip.data.build_dataset`. No real purchasing behaviour is involved.
* Lift is observational co-occurrence. "Buyers of X are N x more likely to
  also buy Z" describes how often the two SKUs share a basket in this history
  relative to independence — it is not a promise of causal uplift, and a
  campaign built on it would still need an A/B test.
* Rules backed by few baskets are kept but flagged ``thin_support`` because
  their metrics are unstable.
* Excel imports carry no order-line data (the template covers products only),
  so mining reports itself unavailable there instead of inventing baskets.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations

from .data import Dataset, build_dataset

# Mining thresholds for the platform's basket table (~650 baskets, 200 SKUs).
# Pair supports are naturally small at this catalogue width, so the support
# floor is low and the thin-support flag does the honesty work.
MIN_SUPPORT = 0.01
MIN_CONFIDENCE = 0.15
MIN_LIFT = 1.25
MAX_LEN = 2  # pairs: single-SKU antecedent -> single-SKU consequent
THIN_SUPPORT_COUNT = 10

DATA_NOTE = (
    "Computed on the seeded synthetic demo data (deterministic order baskets "
    "built in dip/data.py). Lift measures observed co-occurrence, not causation "
    "— it is no promise of sales uplift."
)
UNAVAILABLE_NOTE = (
    "Cross-sell mining needs order-line (basket) data. The Excel import "
    "template covers products and monthly units only, so this view is "
    "available on the seeded synthetic dataset — POST /api/reset to return "
    "to it."
)


# ---------------------------------------------------------------------------
# Frequent-itemset mining (adapted from market-basket-analysis/basket/apriori.py)
# ---------------------------------------------------------------------------


def min_count_for_support(min_support: float, n_baskets: int) -> int:
    """Smallest absolute count c such that c / n_baskets >= min_support.

    The tiny epsilon guards against float products like 0.02 * 6000 landing an
    ulp above the exact integer and inflating the ceiling by one.
    """
    return max(1, math.ceil(min_support * n_baskets - 1e-9))


def _generate_candidates(
    previous_frequent: set[frozenset[str]], k: int
) -> set[frozenset[str]]:
    """Join step + prune step (downward closure) of Apriori."""
    sorted_itemsets = sorted(tuple(sorted(itemset)) for itemset in previous_frequent)
    candidates: set[frozenset[str]] = set()
    for i, a in enumerate(sorted_itemsets):
        for b in sorted_itemsets[i + 1 :]:
            if a[: k - 2] != b[: k - 2]:
                continue  # prefix join: only merge itemsets sharing the first k-2 items
            candidate = frozenset(a) | frozenset(b)
            if len(candidate) != k:
                continue
            # Prune: every (k-1)-subset must itself be frequent.
            if all(
                frozenset(subset) in previous_frequent
                for subset in combinations(sorted(candidate), k - 1)
            ):
                candidates.add(candidate)
    return candidates


def apriori(
    baskets: Sequence[Iterable[str]],
    min_support: float = MIN_SUPPORT,
    max_len: int = MAX_LEN,
) -> dict[frozenset[str], float]:
    """Mine frequent itemsets up to ``max_len`` items.

    Returns a mapping ``itemset -> support`` where support is always
    count / n_baskets. Fully deterministic for a given basket sequence.
    """
    if not 0.0 < min_support <= 1.0:
        raise ValueError("min_support must be in (0, 1]")
    basket_sets = [frozenset(b) for b in baskets]
    n = len(basket_sets)
    if n == 0:
        return {}
    min_count = min_count_for_support(min_support, n)

    item_counts = Counter(item for basket in basket_sets for item in basket)
    current: dict[frozenset[str], int] = {
        frozenset((item,)): count
        for item, count in item_counts.items()
        if count >= min_count
    }
    frequent_items = {item for item, count in item_counts.items() if count >= min_count}
    all_frequent: dict[frozenset[str], int] = dict(current)

    k = 2
    while current and k <= max_len:
        candidates = _generate_candidates(set(current), k)
        counts = dict.fromkeys(candidates, 0)
        # Adaptation vs the reference: enumerate the k-combinations of frequent
        # items present in each basket (baskets are ~10 lines) rather than
        # testing every candidate against every basket.
        for basket in basket_sets:
            present = sorted(basket & frequent_items)
            if len(present) < k:
                continue
            for combo in combinations(present, k):
                key = frozenset(combo)
                if key in counts:
                    counts[key] += 1
        current = {c: cnt for c, cnt in counts.items() if cnt >= min_count}
        all_frequent.update(current)
        k += 1

    return {itemset: count / n for itemset, count in all_frequent.items()}


# ---------------------------------------------------------------------------
# Rule metrics (adapted from market-basket-analysis/basket/rules.py)
# ---------------------------------------------------------------------------


def generate_rules(
    itemsets: Mapping[frozenset[str], float],
    n_baskets: int,
    min_confidence: float = MIN_CONFIDENCE,
    min_lift: float = MIN_LIFT,
    thin_support_count: int = THIN_SUPPORT_COUNT,
) -> list[dict]:
    """Derive filtered, lift-ranked association rules from frequent itemsets.

    Every antecedent/consequent split of every itemset with two or more items
    is scored; rules below ``min_confidence`` or ``min_lift`` are dropped.
    Rules backed by fewer than ``thin_support_count`` baskets are kept but
    flagged ``thin_support`` so consumers can exclude or caveat them. The sort
    (lift desc, confidence desc, then labels) is total, so output order is
    deterministic.
    """
    if n_baskets < 1:
        raise ValueError("n_baskets must be positive")
    rules: list[dict] = []
    for itemset, support in itemsets.items():
        if len(itemset) < 2:
            continue
        items = sorted(itemset)
        for split_size in range(1, len(items)):
            for antecedent_items in combinations(items, split_size):
                antecedent = frozenset(antecedent_items)
                consequent = itemset - antecedent
                support_antecedent = itemsets.get(antecedent)
                support_consequent = itemsets.get(consequent)
                if support_antecedent is None or support_consequent is None:
                    # Cannot happen for complete downward-closed input; guard anyway.
                    continue
                confidence = support / support_antecedent
                lift = confidence / support_consequent
                if confidence < min_confidence or lift < min_lift:
                    continue
                support_count = round(support * n_baskets)
                rules.append(
                    {
                        "antecedent": " + ".join(sorted(antecedent)),
                        "consequent": " + ".join(sorted(consequent)),
                        "support": round(support, 6),
                        "confidence": round(confidence, 4),
                        "lift": round(lift, 4),
                        "support_count": support_count,
                        "thin_support": support_count < thin_support_count,
                    }
                )
    rules.sort(
        key=lambda r: (-r["lift"], -r["confidence"], r["antecedent"], r["consequent"])
    )
    return rules


# ---------------------------------------------------------------------------
# Platform integration: baskets from the dataset -> cached mining result
# ---------------------------------------------------------------------------


def baskets_from_dataset(ds: Dataset) -> list[frozenset[str]]:
    """One frozenset of SKU ids per synthetic customer order event."""
    return [frozenset(o["sku_ids"]) for o in ds.order_lines or []]


def mine_crosssell(
    ds: Dataset | None = None,
    min_support: float = MIN_SUPPORT,
    min_confidence: float = MIN_CONFIDENCE,
    min_lift: float = MIN_LIFT,
    top_overall: int = 50,
) -> dict:
    """Mine the dataset's baskets once; the app caches the result per dataset.

    Returns plain dicts ready for JSON: the lift-ranked rule list, a
    per-product index for the ``?product=`` endpoint/dashboard picker, and the
    honesty note. Datasets without ``order_lines`` (Excel imports) get an
    honest ``available: False`` payload instead of fabricated baskets.
    """
    ds = ds or build_dataset()
    params = {
        "min_support": min_support,
        "min_confidence": min_confidence,
        "min_lift": min_lift,
        "max_len": MAX_LEN,
        "thin_support_count": THIN_SUPPORT_COUNT,
    }
    if not ds.order_lines:
        return {
            "available": False,
            "note": UNAVAILABLE_NOTE,
            "n_baskets": 0,
            "n_rules": 0,
            "params": params,
            "rules": [],
            "products": [],
        }

    baskets = baskets_from_dataset(ds)
    itemsets = apriori(baskets, min_support=min_support, max_len=MAX_LEN)
    rules = generate_rules(
        itemsets, len(baskets), min_confidence=min_confidence, min_lift=min_lift
    )

    meta = {s["sku_id"]: s for s in ds.skus}
    for r in rules:
        r["antecedent_name"] = meta[r["antecedent"]]["name"]
        r["antecedent_category"] = meta[r["antecedent"]]["category"]
        r["consequent_name"] = meta[r["consequent"]]["name"]
        r["consequent_category"] = meta[r["consequent"]]["category"]

    by_product: dict[str, list[dict]] = {}
    for r in rules:
        by_product.setdefault(r["antecedent"], []).append(r)
    products = [
        {
            "sku_id": sku,
            "name": meta[sku]["name"],
            "category": meta[sku]["category"],
            "n_rules": len(rs),
        }
        for sku, rs in sorted(by_product.items())
    ]

    return {
        "available": True,
        "note": DATA_NOTE,
        "n_baskets": len(baskets),
        "n_rules": len(rules),
        "params": params,
        "rules": rules[:top_overall],
        "by_product": by_product,
        "products": products,
    }


def recommendations_for(result: dict, product: str, top: int = 5) -> list[dict]:
    """Top-N cross-sell rules with ``product`` as the antecedent.

    A product with no rules above the thresholds yields an empty list — the
    caller decides how to present that (the API keeps it a 200 with an honest
    note; a 404 is reserved for SKUs the dataset does not contain at all).
    """
    if not result.get("available"):
        return []
    return result.get("by_product", {}).get(product, [])[: max(0, top)]


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    res = mine_crosssell()
    print(f"baskets: {res['n_baskets']}  rules: {res['n_rules']}")
    for r in res["rules"][:5]:
        print(
            f"  {r['antecedent']} -> {r['consequent']}  "
            f"supp {r['support']:.3f}  conf {r['confidence']:.2f}  lift {r['lift']:.2f}"
            f"  ({r['support_count']} baskets{', thin' if r['thin_support'] else ''})"
        )
