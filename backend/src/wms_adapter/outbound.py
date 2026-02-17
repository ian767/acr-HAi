import logging
import uuid
from datetime import datetime

from src.wms_adapter.schemas import WMSInventoryUpdateReport, WMSOrderCompletedReport

logger = logging.getLogger(__name__)


class WMSOutboundClient:
    """Stub client for WES -> WMS outbound communication.

    Currently logs only. Replace with HTTP client when real WMS is available.
    """

    async def report_order_completed(
        self, external_id: str, order_id: uuid.UUID, items_picked: int
    ) -> None:
        report = WMSOrderCompletedReport(
            external_id=external_id,
            order_id=order_id,
            completed_at=datetime.now(),
            items_picked=items_picked,
        )
        logger.info("WMS report: order completed — %s", report.model_dump_json())

    async def report_inventory_update(
        self, sku: str, zone_id: uuid.UUID, qty_change: int, reason: str
    ) -> None:
        report = WMSInventoryUpdateReport(
            sku=sku,
            zone_id=zone_id,
            qty_change=qty_change,
            reason=reason,
        )
        logger.info("WMS report: inventory update — %s", report.model_dump_json())


wms_client = WMSOutboundClient()
