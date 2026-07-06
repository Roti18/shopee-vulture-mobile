"""
Selector halaman checkout, pembayaran, dan sukses order.
Diambil dari ui-layout-product.json.

Ubah file ini jika Shopee update UI — parser dan workflow TIDAK perlu diubah.
"""
from bot.ui.base_selectors import UISelector

# ── Checkout Page ────────────────────────────────────────────────────────────

CHECKOUT_TITLE = UISelector(
    text="Checkout",
    text_exact=True,
    description="Judul halaman checkout",
)

BACK_BUTTON = UISelector(
    resource_id="buttonActionBarBack",
    description="Tombol back di action bar",
)

PLACE_ORDER_BUTTON = UISelector(
    text="Buat Pesanan",
    text_exact=True,
    description="Tombol 'Buat Pesanan' di bottom checkout",
)

PAYMENT_METHOD_SECTION = UISelector(
    resource_id="checkoutPaymentMethod",
    description="Section metode pembayaran (perlu scroll untuk visible)",
)

ORDER_TOTAL_LABEL = UISelector(
    resource_id="labelOrderTotalPrice",
    description="Label total harga order",
)

TOTAL_PAYMENT_LABEL = UISelector(
    resource_id="labelTotalPayment",
    description="Label total pembayaran di bottom",
)

SHOP_NAME_LABEL = UISelector(
    resource_id="labelShopName",
    description="Nama toko di checkout",
)

# ── Payment Page ─────────────────────────────────────────────────────────────

PAYMENT_PAGE_TITLE = UISelector(
    text="Pembayaran",
    text_exact=True,
    class_hierarchy=("android.widget.TextView", ""),
    description="Judul halaman pembayaran",
)

PAYMENT_BACK_BUTTON = UISelector(
    resource_id="buttonActionBarBack",
    description="Tombol back di halaman pembayaran",
)

# ── Order Success Page ───────────────────────────────────────────────────────

ORDER_SUCCESS_TITLE = UISelector(
    resource_id="labelOSPHeaderTitle",
    description="Judul halaman sukses / tertunda setelah order dibuat",
)

ORDER_SUCCESS_SUBTITLE = UISelector(
    resource_id="labelOSPHeaderSubtitle",
    description="Subtitle info order setelah sukses",
)

BUTTON_BERANDA = UISelector(
    text="Beranda",
    text_exact=True,
    class_hierarchy=("android.widget.TextView", "labelOSPHeaderButtons"),
    description="Tombol Beranda di halaman sukses",
)

BUTTON_PESANAN_SAYA = UISelector(
    text="Pesanan Saya",
    text_exact=True,
    class_hierarchy=("android.widget.TextView", "labelOSPHeaderButtons"),
    description="Tombol Pesanan Saya di halaman sukses",
)

# Teks yang menandakan sukses atau pending
SUCCESS_TEXTS = ["Pembayaran Tertunda", "Pesanan Berhasil", "Pesanan Dibuat"]
