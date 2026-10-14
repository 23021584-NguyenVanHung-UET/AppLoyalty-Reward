class LoyaltyError(Exception):
    """A public, stable error without implementation or provider details."""

    def __init__(self, code, message, status=409):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(code)
