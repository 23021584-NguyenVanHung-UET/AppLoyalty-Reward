"""Keep signed query strings out of Django's development access log."""
import logging
import re


class RedactRequestQuery(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(r"(\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^?\s\"]+)\?[^\s\"]+",
                            r"\1", message)
        record.args = ()
        return True
