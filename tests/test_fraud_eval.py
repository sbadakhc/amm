"""
Real-call fraud-detection eval (docs/decisions/0021). Runs a broadened corpus against
the live Safety Agent + Policy Agent pipeline -- not mocks -- to catch regressions or
model-behavior drift over time. Opt-in only (@pytest.mark.fraud_eval, see conftest.py):
skipped in normal `pytest -v` / CI runs, since it costs real API time and money. Run
explicitly with:

    AMM_RUN_FRAUD_EVAL=1 pytest tests/test_fraud_eval.py -v -s

Corpus scope, per docs/decisions/0021: one true-positive (fraud) and one true-negative
(clean) listing per representative alsoug.com category, plus the fraud archetypes
confirmed during issues #54/#55/#57's investigation (advance-fee, prize/lottery,
pay-before-pickup, fake escrow, phishing link, pet-shipping scam) and a handful of
deliberately tricky negatives (legit raffle, business award, currency exchange) that
stress precision, not just recall. Not exhaustive -- real strengthening of this corpus
is expected to come from pilot review-queue data, not further synthetic expansion.

Thresholds are aggregate, not per-case: individual real-call outcomes are inherently
probabilistic (confirmed empirically -- #57's targeted check caught 13/15, not 15/15,
on repeated identical input), so a single miss on one call is expected, not a bug.
"""

import time

import pytest
import requests

from agents.policy_agent import run_policy_agent
from agents.safety_agent import run_safety_agent

pytestmark = pytest.mark.fraud_eval

FRAUD_RULES = {"F001"}

TRUE_POSITIVES = {
    # Advance-fee / pay-before-viewing, one per representative alsoug.com category.
    "cars_no_viewing_deposit": (
        "بيع سريع جدا تويوتا هايلكس 2020 بدون معاينة",
        "السيارة بالخارج حاليا وسيتم شحنها فور استلام العربون، أرسل 200 دولار "
        "لحجز السيارة قبل ان يشتريها غيرك، لا حاجة للمعاينة أو الفحص.",
    ),
    "realestate_advance_fee": (
        "شقة فاخرة للبيع بسعر مغري جدا - بري",
        "شقة 3 غرف في بري بسعر لا يصدق، البائع مسافر ويريد بيع سريع. أرسل عربون "
        "500 دولار عبر تحويل بنكي لحجز الشقة قبل ان يشتريها غيرك، لا حاجة لمعاينة العقار.",
    ),
    "jobs_advance_fee": (
        "وظيفة شاغرة - راتب 3000 دولار شهريا بدون خبرة",
        "مطلوب موظفين للعمل من المنزل، راتب مضمون 3000 دولار شهريا. ارسل 50 دولار "
        "رسوم تسجيل وبيانات حسابك البنكي عبر واتساب لتأكيد التوظيف فورا.",
    ),
    "electronics_phishing_link": (
        "ايفون 16 برو ماكس مجانا - عرض محدود",
        "اضغط على الرابط التالي وأدخل بيانات بطاقتك البنكية لتأكيد استلام الجهاز "
        "المجاني قبل انتهاء العرض خلال 24 ساعة.",
    ),
    "services_fake_escrow": (
        "خدمة تصميم مواقع احترافية - دفعة كاملة مقدما",
        "ادفع كامل المبلغ مقدما عبر تحويل بنكي لضمان حجز موعدك، لا نبدأ العمل الا "
        "بعد استلام كامل المبلغ، لا يوجد ضمان استرجاع.",
    ),
    "barter_upfront_shipping_fee": (
        "أقايض ايفون 16 برو ماكس بسيارتك",
        "أقايض هاتفي الجديد بسيارتك، سيتم شحن الهاتف اليك فور استلام رسوم الشحن "
        "والتأمين 50 دولار عبر واتساب.",
    ),
    "home_furniture_urgent_move_scam": (
        "عفش فاخر مجانا - انتقال عاجل خارج البلاد",
        "نغادر البلاد غدا ونوزع كل الأثاث مجانا، فقط أرسل رسوم النقل 80 دولار "
        "مقدما عبر تحويل بنكي لحجز قطعتك قبل غيرك.",
    ),
    "animals_pet_shipping_scam": (
        "جراء هاسكي نادرة للتبني المجاني",
        "جراء هاسكي أصيلة، سيتم شحنها لك عبر شركة شحن معتمدة، أرسل رسوم الشحن "
        "والتطعيم 60 دولار أولا لتأكيد الحجز، الجرو غير متاح للمعاينة حاليا.",
    ),
    "industrial_equipment_advance_payment": (
        "حفارة مستعملة للبيع بسعر مغري جدا",
        "حفارة بحالة ممتازة، البائع بالخارج حاليا، أرسل دفعة أولى 500 دولار "
        "لحجزها قبل الشحن، لا يوجد فحص أو معاينة متاحة قبل الدفع.",
    ),
    "general_goods_lottery": (
        "لقد ربحت جائزة السوق الشهرية!",
        "تهانينا لقد تم اختيارك للفوز بجائزة نقدية قدرها 10000 دولار، لاستلام "
        "الجائزة يرجى دفع رسوم شحن 100 دولار عبر التحويل البنكي.",
    ),
    "lottery_english_phrasing": (
        "CONGRATULATIONS! You've won our monthly prize draw!",
        "You have been selected to win a cash prize of $10,000. To claim your "
        "prize, please pay a $100 shipping fee via bank transfer.",
    ),
}

TRUE_NEGATIVES = {
    # One clean listing per category matched to the positives above.
    "cars_clean": ("تويوتا هايلكس 2018 للبيع", "سيارة تويوتا هايلكس موديل 2018، حالة ممتازة، فحص كامل، جاهزة للبيع."),
    "realestate_clean": ("شقة للايجار في بري", "شقة 2 غرفة وصالة في بري، مفروشة بالكامل، قريبة من الخدمات، للمعاينة اتصل بنا."),
    "jobs_clean": ("مطلوب محاسب - شركة تجارية بالخرطوم", "شركة شندي للتجارة العامة تطلب محاسب بخبرة، الراتب حسب الخبرة، يرجى إرسال السيرة الذاتية."),
    "electronics_clean": ("iPhone 15 Pro Max 256GB للبيع", "هاتف ايفون 15 برو ماكس استخدام خفيف، بدون خدوش، مع جميع الملحقات الأصلية."),
    "services_clean": ("خدمات صيانة مكيفات", "فني صيانة مكيفات معتمد، تركيب وصيانة جميع الأنواع، خدمة سريعة."),
    "barter_clean": ("أقايض دراجة هوائية بلابتوب", "دراجة هوائية بحالة جيدة جدا، أرغب بمقايضتها بلابتوب مناسب، التواصل مباشرة للمعاينة."),
    "home_furniture_clean": ("طقم كنب 7 مقاعد للبيع", "طقم كنب مستعمل بحالة ممتازة، خشب زان، للمعاينة في المنزل."),
    "animals_clean": ("قطط شيرازي للبيع", "قطط شيرازي أصيلة، تم تطعيمها بالكامل، للمعاينة في المنزل قبل الشراء."),
    "industrial_equipment_clean": ("مولد كهرباء 20 كيلو للبيع", "مولد كهرباء بحالة ممتازة، فحص متاح، للمعاينة في الموقع."),
    "general_goods_clean": ("أدوات مطبخ ستانلس ستيل للبيع", "طقم أدوات مطبخ جديد، لم يستخدم، سعر مناسب."),
    # Deliberately tricky negatives -- legitimate content that shares surface features
    # with the fraud patterns above, to stress precision, not just recall.
    "legit_raffle_disclosed": ("اشترك في السحب الشهري بشراء تذكرة", "اشترك في السحب الشهري بشراء تذكرة بـ 10 جنيه، السحب مباشر وموثق، الفائز يعلن على صفحتنا."),
    "business_award_brag": ("فخورون بفوزنا بجائزة أفضل متجر", "فخورون بفوزنا بجائزة أفضل متجر إلكترونيات لعام 2025 في السودان."),
    "currency_exchange_legit": ("صرافة عملات بسعر تنافسي", "نوفر خدمة صرافة دولار ويورو وريال بأفضل سعر في السوق، تواصل واتساب."),
    "vague_investment_pitch": ("فرصة استثمارية سريعة الربح", "استثمر أموالك معنا واحصل على أرباح مضاعفة خلال أسبوع، فرصة محدودة."),
}


def _run(title: str, description: str, listing_id: str) -> tuple[list[str], dict]:
    """A 25-case corpus makes ~35-45 real API calls per run -- more exposed to a
    transient network blip than any single test, so retry the whole case (not just one
    HTTP call) once on a connection-level failure rather than failing the entire eval
    run over one timeout."""
    canonical_doc = {"listingId": listing_id, "title": title, "description": description}
    last_error = None
    for _attempt in range(3):
        try:
            safety = run_safety_agent(canonical_doc)
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            time.sleep(2)
    else:
        raise last_error
    policy = run_policy_agent(
        canonical_doc,
        evidence={"brandMismatch": False},
        consistency={"inconsistencyScore": 0.05},
        safety=safety["payload"],
    )
    matched_rules = {m["rule"] for m in policy["payload"]["matches"]}
    return sorted(matched_rules), safety["payload"]


def test_fraud_corpus_recall_and_precision():
    hits, misses, false_positives = [], [], []

    for name, (title, description) in TRUE_POSITIVES.items():
        matched_rules, safety_payload = _run(title, description, f"EVAL-TP-{name}")
        caught = bool(set(matched_rules) & FRAUD_RULES)
        print(f"[TP] {name}: matched={matched_rules} violations={safety_payload['violations']} caught={caught}")
        (hits if caught else misses).append(name)

    for name, (title, description) in TRUE_NEGATIVES.items():
        matched_rules, safety_payload = _run(title, description, f"EVAL-TN-{name}")
        flagged = bool(set(matched_rules) & FRAUD_RULES)
        print(f"[TN] {name}: matched={matched_rules} violations={safety_payload['violations']} flagged={flagged}")
        if flagged:
            false_positives.append(name)

    recall = len(hits) / len(TRUE_POSITIVES)
    fp_rate = len(false_positives) / len(TRUE_NEGATIVES)
    print(f"\nRecall: {len(hits)}/{len(TRUE_POSITIVES)} ({recall:.0%}) -- missed: {misses}")
    print(f"False positive rate: {len(false_positives)}/{len(TRUE_NEGATIVES)} ({fp_rate:.0%}) -- flagged: {false_positives}")

    # Aggregate, not per-case -- single-call outcomes are inherently probabilistic
    # (docs/decisions/0020 measured 13/15 on repeated identical input). These
    # thresholds are a regression floor, not a target: investigate any run that drops
    # meaningfully below them, but don't expect 100% on any single pass.
    assert recall >= 0.6, f"Fraud recall dropped to {recall:.0%} -- investigate for model drift"
    assert fp_rate <= 0.2, f"False positive rate rose to {fp_rate:.0%} -- investigate for over-triggering"
