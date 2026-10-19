class SourceError(RuntimeError):
    def __init__(self, code, message, status_code=400):
        self.code, self.status_code = code, status_code
        super().__init__(message)

    def as_error_dict(self):
        return {"code": self.code, "message": str(self), "stage": "source_import", "retryable": False}
