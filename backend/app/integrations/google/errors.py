class GoogleError(Exception):
    """Safe public errors only; provider bodies and token values must never be attached."""

    def __init__(self, code: str, detail: str, status: int = 502):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
