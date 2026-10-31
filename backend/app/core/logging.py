import logging


class RedactOAuthQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if isinstance(args[2], str) and "/oauth/google/callback" in args[2]:
                args[2] = args[2].split("?", 1)[0]
                record.args = tuple(args)
        return True
