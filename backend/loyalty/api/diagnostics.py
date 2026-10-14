"""Expose fixed diagnostic messages, never arbitrary stored provider text."""
SAFE_MESSAGES = {
    "SHOPIFY_TRANSIENT": "Shopify is temporarily unavailable. Issuance will be retried.",
    "ISSUE_FAILED": "The discount could not be issued. Points were refunded.",
    "PROCESSING_FAILED": "Processing failed. Review the masked payload and retry if appropriate.",
    "EVENT_SKIPPED": "This event was skipped. Review the topic and masked payload before retrying.",
}


def redemption_error(value):
    if not value:
        return ""
    code = value.partition(":")[0]
    return SAFE_MESSAGES.get(code, "Issuance has not completed. Review its status before retrying.")


def webhook_error(status):
    return SAFE_MESSAGES["PROCESSING_FAILED"] if status == "FAILED" else ""
