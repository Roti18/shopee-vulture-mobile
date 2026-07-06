"""
Dataclass konfigurasi produk yang dimonitor.

Perubahan dari v1:
  - target_stock  → minimum_stock + purchase_quantity + stock_mode
"""
from dataclasses import dataclass


@dataclass
class ProductConfig:
    url: str = ""
    name: str = ""
    variant: str = ""                           # keyword variant target
    payment_method: str = "SeaBank Virtual Account"

    # Stock threshold
    minimum_stock: int = 1                      # stok minimum sebelum checkout
    purchase_quantity: int = 1                  # jumlah item yang dibeli
    stock_mode: str = "any"                     # "any" | "minimum"

    restock_limit: int = 3                      # max pembelian per sesi

    def is_valid(self) -> bool:
        return bool(self.url and self.name)

    def stock_meets_threshold(self, stock_count: int) -> bool:
        """
        Apakah jumlah stok memenuhi syarat untuk checkout?

        stock_mode="any"     → stock_count > 0
        stock_mode="minimum" → stock_count >= minimum_stock
        """
        if stock_count <= 0:
            return False
        if self.stock_mode == "any":
            return True
        # "minimum"
        return stock_count >= self.minimum_stock

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "name": self.name,
            "variant": self.variant,
            "payment_method": self.payment_method,
            "minimum_stock": self.minimum_stock,
            "purchase_quantity": self.purchase_quantity,
            "stock_mode": self.stock_mode,
            "restock_limit": self.restock_limit,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProductConfig":
        return cls(
            url=data.get("url", ""),
            name=data.get("name", ""),
            variant=data.get("variant", ""),
            payment_method=data.get("payment_method", "SeaBank Virtual Account"),
            minimum_stock=int(data.get("minimum_stock", data.get("target_stock", 1))),
            purchase_quantity=int(data.get("purchase_quantity", 1)),
            stock_mode=data.get("stock_mode", "any"),
            restock_limit=int(data.get("restock_limit", 3)),
        )
