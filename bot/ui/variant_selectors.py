"""
Selector popup varian Shopee.
Diambil dari ui-layout-product.json.

Ubah file ini jika Shopee update UI — parser dan workflow TIDAK perlu diubah.
"""
from bot.ui.base_selectors import UISelector

# ── Container Popup ──────────────────────────────────────────────────────────

VARIANT_CONTAINER = UISelector(
    resource_id="sectionTierVariation",
    description="Container popup pemilihan varian",
)

CLOSE_POPUP_BUTTON = UISelector(
    resource_id="buttonClose",
    content_desc="Tutup",
    description="Tombol X tutup popup varian",
)

# ── Variant Item ─────────────────────────────────────────────────────────────

# resource_id varian bersifat dynamic (imageItemIcon_id-<hash>),
# sehingga tidak bisa dipakai sebagai selector tetap.
# Gunakan class_hierarchy + text_regex untuk menemukan varian.

VARIANT_ITEM_CLASS = "android.widget.ImageView"   # class node gambar varian
VARIANT_STOCK_PARENT = "sectionTierVariation"

STOCK_TEXT_REGEX = r"Stok:\s*(\d+)"               # Level 5: regex
STOCK_OUT_OVERLAY = UISelector(
    text="Habis",
    text_exact=True,
    class_hierarchy=("android.widget.TextView", "sectionTierVariation"),
    description="Overlay 'Habis' pada thumbnail varian yang kosong",
)

# ── Harga di Popup ───────────────────────────────────────────────────────────

# Tidak ada resource_id tetap untuk harga di popup;
# pakai class_hierarchy untuk menemukan TextView harga di dalam popup.
POPUP_PRICE_LABEL = UISelector(
    class_hierarchy=("android.widget.TextView", "sectionTierVariation"),
    text_regex=r"Rp[\d.,]+",
    description="Label harga di dalam popup varian",
)

# ── Quantity Controls ────────────────────────────────────────────────────────

BUTTON_MINUS = UISelector(
    resource_id="buttonMinus",
    description="Tombol kurangi jumlah",
)

BUTTON_PLUS = UISelector(
    resource_id="buttonPlus",
    description="Tombol tambah jumlah",
)

QUANTITY_INPUT = UISelector(
    class_hierarchy=("android.widget.EditText", "sectionTierVariation"),
    description="Input jumlah beli",
)

# ── Submit Button ────────────────────────────────────────────────────────────

SUBMIT_BUTTON = UISelector(
    resource_id="buttonCartPanelSubmit",
    description="Tombol submit (Beli Sekarang / Habis)",
)

# Teks submit yang menandakan stok tersedia
SUBMIT_TEXT_READY = "Beli Sekarang"
SUBMIT_TEXT_EMPTY = "Habis? Temukan Produk Lainnya"
