"""Console mail must not put private reward codes or addresses in terminal logs."""
from django.core.mail.backends.console import EmailBackend as ConsoleBackend


class RedactedConsoleBackend(ConsoleBackend):
    def write_message(self, message):
        self.stream.write("Loyalty email rendered (console only; recipient and body redacted).\n")
        self.stream.flush()
