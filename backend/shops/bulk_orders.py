"""Bulk query transport and a disk-backed JSONL graph, with no per-order calls."""
import json
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from .shopify import ShopifyError, ShopifyTransientError, graphql, numeric_id

FIELDS = "id status url partialDataUrl errorCode completedAt"
CURRENT = "query ImportCurrent { currentBulkOperation(type: QUERY) { " + FIELDS + " } }"
BUSY = """query ImportBusy {
  bulkOperations(first: 1, query: "operation_type:query (status:created OR status:running OR status:canceling)") {
    nodes { id status }
  }
}"""
BY_ID = "query ImportBulk($id: ID!) { node(id: $id) { ... on BulkOperation { " + FIELDS + " } } }"
START = """mutation ImportStart($query: String!) {
  bulkOperationRunQuery(query: $query) { bulkOperation { id status } userErrors { field message } }
}"""
ACTIVE = {"CREATED", "RUNNING", "CANCELING"}


def require_order_scopes(since, now):
    from datetime import date, timedelta
    if date.fromisoformat(since) < (now-timedelta(days=60)).date():
        installation = graphql("{ currentAppInstallation { accessScopes { handle } } }").get("currentAppInstallation") or {}
        scopes = {row["handle"] for row in installation.get("accessScopes", [])}
        if "read_all_orders" not in scopes or not scopes.intersection({"read_orders", "write_orders"}):
            raise ShopifyError("READ_ALL_ORDERS_REQUIRED")


class ResultExpired(ShopifyError):
    pass


class OperationFailed(ShopifyError):
    pass


def bulk_query(since, until, stage):
    search = json.dumps(f"created_at:>={since}T00:00:00Z created_at:<{until}T00:00:00Z")
    # Split into two sequential operations: Shopify caps a bulk query at five
    # connections. Refund shipping must not silently disappear from calculation.
    fields = """
      id __typename createdAt updatedAt test cancelledAt displayFinancialStatus taxesIncluded currencyCode sourceName
      customer { id email createdAt } customAttributes { key value }
      transactions { id gateway kind status amountSet { shopMoney { amount currencyCode } } }
      lineItems { edges { node {
        id __typename quantity isGiftCard originalTotalSet { shopMoney { amount currencyCode } }
        discountAllocations { allocatedAmountSet { shopMoney { amount currencyCode } } }
        taxLines { priceSet { shopMoney { amount currencyCode } } }
        product { id collections { edges { node { id __typename } } } }
      } } }
      refunds { id refundLineItems { edges { node {
        id __typename lineItem { id } quantity subtotalSet { shopMoney { amount currencyCode } }
        totalTaxSet { shopMoney { amount currencyCode } }
      } } } }
    """ if stage == "core" else """
      id __typename updatedAt
      shippingLines { edges { node { id __typename
        discountedPriceSet { shopMoney { amount currencyCode } }
        taxLines { priceSet { shopMoney { amount currencyCode } } }
      } } }
      refunds { id refundShippingLines { edges { node { id __typename
        subtotalAmountSet { shopMoney { amount currencyCode } }
        taxAmountSet { shopMoney { amount currencyCode } }
      } } } }
    """
    return "{ orders(query: " + search + ", sortKey: ID) { edges { node { " + fields + " } } } }"


def current_operation():
    return graphql(CURRENT).get("currentBulkOperation")


def start_operation(query):
    current = current_operation()
    if current and current["status"] in ACTIVE:
        raise ShopifyTransientError("BULK_BUSY")
    if graphql(BUSY)["bulkOperations"]["nodes"]:
        raise ShopifyTransientError("BULK_BUSY")
    result = graphql(START, {"query": query})["bulkOperationRunQuery"]
    if result.get("userErrors") or not result.get("bulkOperation"):
        raise ShopifyError("BULK_QUERY_REJECTED")
    return result["bulkOperation"]["id"]


def wait_operation(operation_id):
    deadline = time.monotonic() + settings.LOYALTY_IMPORT_POLL_TIMEOUT
    while True:
        current = current_operation()
        # New API versions allow concurrent operations. Never consume another job.
        result = current if current and current.get("id") == operation_id else graphql(BY_ID, {"id": operation_id}).get("node")
        if not result:
            raise ResultExpired("BULK_OPERATION_EXPIRED")
        status = result["status"]
        if status == "COMPLETED":
            return result.get("url")  # None is valid for an empty result.
        if status not in ACTIVE:
            # partialDataUrl is deliberately never imported as a complete dataset.
            raise OperationFailed("BULK_" + status)
        if time.monotonic() >= deadline:
            raise ShopifyTransientError("BULK_POLL_TIMEOUT")
        time.sleep(min(30, max(1, settings.LOYALTY_IMPORT_POLL_SECONDS)))


def download_lines(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not (
        host in ("storage.googleapis.com", "cdn.shopify.com") or host.endswith(".storage.googleapis.com")):
        raise ShopifyError("BULK_RESULT_HOST_REJECTED")
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=False) as response:
            if response.status_code in (403, 404, 410):
                raise ResultExpired("BULK_RESULT_EXPIRED")
            if response.status_code != 200:
                raise ShopifyTransientError("BULK_DOWNLOAD_FAILED")
            for line in response.iter_lines():
                if len(line) > 10_000_000:
                    raise ShopifyError("BULK_LINE_TOO_LARGE")
                if line.strip():
                    yield line
    except httpx.RequestError:
        # Signed download URLs and response content must never reach logs/errors.
        raise ShopifyTransientError("BULK_DOWNLOAD_FAILED") from None


class JSONLGraph:
    def __init__(self, filename):
        self.db = sqlite3.connect(filename)
        self.db.executescript("""
          CREATE TABLE nodes (seq INTEGER PRIMARY KEY, stage TEXT, id TEXT, parent TEXT, kind TEXT, payload TEXT);
          CREATE INDEX parents ON nodes(stage,parent,kind);
          CREATE INDEX roots ON nodes(stage,kind,parent,id);
          CREATE TABLE affected (id INTEGER PRIMARY KEY);
        """)

    def close(self):
        self.db.close()

    def ingest(self, lines, stage):
        for line in lines:
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not row.get("id"):
                    raise ValueError()
                numeric_id(row["id"])
                kind = row.get("__typename") or str(row["id"]).split("/")[-2]
                self.db.execute("INSERT INTO nodes(stage,id,parent,kind,payload) VALUES (?,?,?,?,?)",
                    [stage, row["id"], row.get("__parentId"), kind, json.dumps(row)])
            except (ValueError, TypeError, KeyError):
                raise ShopifyError("INVALID_BULK_JSONL") from None
        self.db.commit()

    def children(self, parent, kind, stage):
        rows = self.db.execute("SELECT payload FROM nodes WHERE stage=? AND parent=? AND kind=? ORDER BY seq", [stage,parent,kind])
        # Shopify can repeat a shared product's collection edges across orders.
        result = {}
        for (source,) in rows:
            row = json.loads(source)
            result[row["id"]] = row
        return list(result.values())

    def orders(self, after_id=0):
        roots = self.db.execute("SELECT payload FROM nodes WHERE stage='core' AND kind='Order' AND parent IS NULL ORDER BY CAST(substr(id, instr(id, '/Order/')+7) AS INTEGER)")
        for (source,) in roots:
            order = json.loads(source)
            if numeric_id(order["id"]) <= after_id:
                continue
            shipping = self.db.execute("SELECT payload FROM nodes WHERE stage='shipping' AND kind='Order' AND parent IS NULL AND id=?", [order["id"]]).fetchone()
            if not shipping:
                raise ShopifyError("BULK_STAGES_INCONSISTENT")
            supplemental = json.loads(shipping[0])
            if order.get("updatedAt") != supplemental.get("updatedAt"):
                raise ShopifyError("BULK_STAGES_INCONSISTENT")
            lines = self.children(order["id"], "LineItem", "core")
            for line in lines:
                product = line.get("product")
                if product:
                    product["collections"] = {"nodes": self.children(product["id"], "Collection", "core")}
            order["lineItems"] = {"nodes": lines}
            order["shippingLines"] = {"nodes": self.children(order["id"], "ShippingLine", "shipping")}
            extra_refunds = {r["id"] for r in supplemental.get("refunds", [])}
            if extra_refunds != {r["id"] for r in order.get("refunds", [])}:
                raise ShopifyError("BULK_STAGES_INCONSISTENT")
            for refund in order.get("refunds", []):
                refund["refundLineItems"] = {"nodes": self.children(refund["id"], "RefundLineItem", "core")}
                refund["refundShippingLines"] = {"nodes": self.children(refund["id"], "RefundShippingLine", "shipping")}
            yield order


@contextmanager
def graph():
    # SQLite spills graph nodes to a private temporary file, not an in-memory list.
    with tempfile.NamedTemporaryFile(prefix="loyalty-import-", suffix=".sqlite3", delete_on_close=False) as file:
        store = JSONLGraph(file.name)
        try:
            yield store
        finally:
            store.close()
