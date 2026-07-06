"""
Selector halaman produk Shopee.
Diambil dari ui-layout-product.json (resolusi referensi: 1080x2265).

Ubah file ini jika Shopee update UI — parser dan workflow TIDAK perlu diubah.
"""
from bot.ui.base_selectors import UISelector

# ── Navigasi ────────────────────────────────────────────────────────────────

BACK_BUTTON = UISelector(
    resource_id="buttonActionBarBack",
    description="Tombol back di action bar",
)

# ── Status Stok (Level 1 check) ─────────────────────────────────────────────

STOCK_OUT_LABEL = UISelector(
    text="Habis",
    text_exact=True,
    # Kalau text tidak ketemu, cari di dalam area image section
    class_hierarchy=("android.widget.TextView", "sectionProductImages"),
    description="Label 'Habis' di halaman produk (stok kosong)",
)

FIND_OTHER_PRODUCTS = UISelector(
    text="Habis? Temukan Produk Lainnya",
    text_exact=True,
    description="Link temukan produk lain (muncul saat stok habis)",
)

# ── Section Identifiers ──────────────────────────────────────────────────────

PRODUCT_IMAGES_SECTION = UISelector(
    resource_id="sectionProductImages",
    description="Container gambar produk",
)

PRICE_SECTION = UISelector(
    resource_id="sectionProductPrice",
    description="Section harga produk",
)

PRODUCT_NAME_LABEL = UISelector(
    resource_id="labelProductPageProductName",
    description="Label nama produk",
)

PROMO_SECTION = UISelector(
    resource_id="labelPromotions",
    description="Section promo/voucher",
)

COUNTDOWN_TIMER = UISelector(
    resource_id="sectionCounter",
    description="Flash sale countdown timer",
)

# ── Bottom Action Buttons ────────────────────────────────────────────────────

CHAT_BUTTON = UISelector(
    resource_id="buttonProductChatNow",
    content_desc="Chat",
    description="Tombol chat seller",
)

ADD_TO_CART_BUTTON = UISelector(
    resource_id="buttonProductAddCart",
    description="Tombol tambah ke keranjang",
)

BUY_NOW_BUTTON = UISelector(
    resource_id="buttonProductBuyNow",
    text="Beli Dengan Voucher",
    text_exact=False,
    description="Tombol 'Beli Dengan Voucher' / 'Beli Sekarang' di halaman produk",
)
