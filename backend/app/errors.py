class AppError(Exception):
    def __init__(self, code: str, status: int = 400, retryable: bool = False, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
