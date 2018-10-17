import logging
try:
    import systemd.journal
    try:
        logging_handler = systemd.journal.JournalHandler()
    except AttributeError:
        # structure of module changed? check debian-packages?
        logging_handler = systemd.journal.JournaldLogHandler()
except ImportError:
    # Fall back to a standard syslog handler
    import logging.handlers
    logging_handler = logging.handlers.SysLogHandler()

def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(logging_handler)
    logger.addHandler(logging.StreamHandler())
    return logger
