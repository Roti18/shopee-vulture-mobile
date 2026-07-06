"""
Enumerasi utama bot: state machine, mode, status stok, level recovery, stock mode.
"""
from enum import Enum


class BotMode(Enum):
    IDLE = "idle"
    RUNNING = "running"
    MONITOR = "monitor"
    PAUSED = "paused"
    STOPPED = "stopped"
    BLACKOUT = "blackout"
    COOLDOWN = "cooldown"


class WorkflowState(Enum):
    """
    State machine flow (CHECK_STOCK dihapus — stok hanya valid dari popup varian):

    IDLE → OPEN_PRODUCT → BUY_VOUCHER → CHECK_VARIANT → BUY_NOW
         → CHECKOUT → VERIFY_PAYMENT → CREATE_ORDER
         → COOLDOWN | OPEN_PRODUCT
    Any failure → RECOVERY → OPEN_PRODUCT
    """
    IDLE = "idle"
    OPEN_PRODUCT = "open_product"
    BUY_VOUCHER = "buy_voucher"
    CHECK_VARIANT = "check_variant"
    BUY_NOW = "buy_now"
    CHECKOUT = "checkout"
    VERIFY_PAYMENT = "verify_payment"
    CREATE_ORDER = "create_order"
    SUCCESS = "success"
    COOLDOWN = "cooldown"
    RECOVERY = "recovery"


class StockStatus(Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class StockMode(str, Enum):
    """
    Mode pengecekan stok minimum sebelum checkout.
    - ANY     : stock > 0 cukup untuk checkout
    - MINIMUM : stock >= minimum_stock disyaratkan
    """
    ANY = "any"
    MINIMUM = "minimum"


class RecoveryLevel(Enum):
    L1_SOFT_RETRY = 1
    L2_ADB_RECONNECT = 2
    L3_FORCE_STOP_APP = 3
    L4_RESTART_ADB_SERVER = 4
    L5_PANIC = 5


class ScreenType(Enum):
    PRODUCT_PAGE = "product_page"
    VARIANT_POPUP = "variant_popup"
    CHECKOUT_PAGE = "checkout_page"
    PAYMENT_PAGE = "payment_page"
    ORDER_SUCCESS = "order_success"
    UNKNOWN = "unknown"
